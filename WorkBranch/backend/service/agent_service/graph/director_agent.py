"""
Director Agent - 统一编排图

完全合并工具执行逻辑，确保上下文正确传递。

参考 Claude Code 架构：
1. 循环判断：使用块类型（tool_use/chat）驱动，而非状态机
2. Plan 模式：生成计划写入文件，输出给用户，graph 结束
3. Execute 模式：按步骤执行
4. 最终回复：chat 工具输出，打破循环
"""
from typing import Literal, Optional, Dict, Any, List, Callable
from langgraph.graph import StateGraph, END
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import os
import re
import shutil
import fnmatch

from .decision.complexity_analyzer import ExecutionMode
from ..state import AgentState
from .subgraphs.tool_registry import (
    is_tool_allowed, get_allowed_tools, _write_tool_event
)
from .subgraphs.tool_executor import run_tool_execution
from service.agent_service.prompts.graph_prompts import (
    THINK_SYSTEM_PROMPT,
    PLAN_MODE_SYSTEM_PROMPT,
    build_chat_system_prompt as _graph_build_chat_system_prompt,
    build_context_prompt as _graph_build_context_prompt,
    build_direct_chat_messages as _graph_build_direct_chat_messages,
    build_director_plan_messages,
    build_tool_schema_prompt as _graph_build_tool_schema_prompt,
    format_todo_prompt_block as _graph_format_todo_prompt_block,
)
from service.session_service.canonical import SegmentType
from service.agent_service.service.plan_file_service import plan_file_service
from service.agent_service.service.workspace_service import WorkspaceService
from service.session_service.message_content import build_prompt_safe_text, get_message_parts, get_message_text, has_image_parts
from core.logging import console
from singleton import get_workspace_service

MAX_REPLAN_COUNT = 3
MAX_MESSAGES = 10
MAX_DIRECT_ITERATIONS = 32
CHECK_INTERVAL = 8

workspace_service = get_workspace_service()


def _build_loop_check_prompt(
    tool_history: list, 
    iteration_count: int,
    user_message: str = "",
    conversation_history: list = None,
    todos: list = None,
) -> str:
    recent_history = tool_history[-CHECK_INTERVAL:] if len(tool_history) >= CHECK_INTERVAL else tool_history
    
    history_lines = []
    for idx, item in enumerate(recent_history, 1):
        tool_name = item.get("tool", "unknown")
        args = item.get("args", {})
        args_str = str(args)[:100] if args else "{}"
        result_preview = str(item.get("result", ""))[:200]
        history_lines.append(
            f"第{idx}轮: 工具={tool_name}, 参数={args_str}, 结果摘要={result_preview}..."
        )
    history_block = "\n".join(history_lines) if history_lines else "(暂无工具调用历史)"
    
    user_message_block = ""
    if user_message:
        user_message_block = f"""
## 用户原始请求
{user_message[:500]}
"""
    
    conversation_block = ""
    if conversation_history:
        conv_lines = []
        for msg in conversation_history[-6:]:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))[:300]
            conv_lines.append(f"[{role}]: {content}")
        if conv_lines:
            conversation_block = f"""
## 对话历史
{chr(10).join(conv_lines)}
"""
    
    todos_block = ""
    if todos:
        todo_lines = []
        for idx, todo in enumerate(todos[:10], 1):
            status = todo.get("status", "pending")
            content = str(todo.get("content", ""))[:100]
            todo_lines.append(f"{idx}. [{status}] {content}")
        if todo_lines:
            todos_block = f"""
## 待办事项
{chr(10).join(todo_lines)}
"""
    
    prompt = f"""你是一个任务执行监控器。请分析以下信息，判断任务执行是否存在循环或卡死情况。
{user_message_block}{conversation_block}{todos_block}
## 最近{len(recent_history)}轮工具调用历史
{history_block}

## 当前状态
- 已执行轮次: {iteration_count}

## 判断标准
1. **循环**: 连续多次调用相同工具，使用相同或非常相似的参数，且结果没有实质进展
2. **卡死**: 工具调用失败后反复重试，或在一个无效状态中无法跳出
3. **正常**: 工具调用有变化，或正在逐步推进任务，或者正在处理复杂任务需要更多步骤

## 重要提示
- 如果工具调用正在推进任务（例如：创建目录后创建文件，读取文件后修改内容），应判断为"正常"
- 如果用户请求是复杂任务（如创建项目、多文件修改），可能需要较多工具调用，应判断为"正常"
- 只有在明确看到重复调用相同工具且无进展时，才判断为"循环"

## 输出要求
请以JSON格式返回判断结果：
- 如果判断为循环或卡死，返回: {{"action": "stop", "reason": "具体原因"}}
- 如果判断为正常，返回: {{"action": "continue", "reason": "简要说明"}}

只返回JSON，不要其他内容。"""
    
    return prompt


def _check_loop_or_stuck(
    tool_history: list,
    iteration_count: int,
    llm_service,
    user_message: str = "",
    conversation_history: list = None,
    todos: list = None,
) -> dict:
    from service.agent_service.service.llm_service import LLMService
    
    prompt = _build_loop_check_prompt(
        tool_history, 
        iteration_count,
        user_message=user_message,
        conversation_history=conversation_history,
        todos=todos,
    )
    
    try:
        if isinstance(llm_service, LLMService):
            response = llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        else:
            response = llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        
        result = json.loads(response)
        return result
    except Exception as e:
        return {"action": "continue", "reason": f"检查失败: {str(e)}"}


def _emit_final_reply(reply: str, message_context: dict = None) -> None:
    if not message_context:
        return
    send_message = message_context.get("send_message")
    if not send_message:
        return
    send_message("", SegmentType.CHAT_START, {
        "task_description": "输出最终回复",
        "is_start": True,
    })
    if reply:
        send_message(reply, SegmentType.CHAT_DELTA, {
            "task_description": "输出最终回复",
            "is_delta": True,
        })
    send_message("", SegmentType.CHAT_END, {
        "task_description": "输出最终回复",
        "is_end": True,
        "result": reply,
    })


def _build_chat_system_prompt(settings_service=None) -> str:
    return _graph_build_chat_system_prompt(settings_service)


def _supports_native_multimodal(settings_service=None) -> bool:
    if settings_service is None:
        return False
    try:
        return bool(settings_service.get("llm:supports_vision"))
    except Exception:
        return False


def _should_use_native_multimodal_chat(state: AgentState, settings_service=None) -> bool:
    current_agent_type = state.get("agent_type") or "director_agent"
    if current_agent_type != "director_agent":
        return False
    user_message_parts = state.get("current_user_message_parts") or get_last_user_message_parts(state)
    return _supports_native_multimodal(settings_service) and has_image_parts(user_message_parts)


def _build_native_multimodal_chat_task(state: AgentState) -> dict:
    user_message = state.get("current_user_message_text") or get_last_user_message_text(state)
    user_message_parts = state.get("current_user_message_parts") or get_last_user_message_parts(state)
    chat_task = user_message or "请直接分析这张图片并回答用户。"
    tool_args = {
        "description": chat_task,
        "multimodal_parts": user_message_parts,
    }
    return {
        "pending_tools": [{"tool": "chat", "args": tool_args}],
        "has_tool_use": True,
        "next_action": {
            "kind": "tool",
            "tool_name": "chat",
            "tool_args": tool_args,
            "task_description": chat_task,
        },
        "mode_reason": "检测到图片输入，DIRECT 模式直接走原生多模态 chat",
    }


def _build_direct_chat_messages(
    task_description: str,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    multimodal_parts: Optional[List[dict]] = None,
    message_context: Optional[dict] = None,
) -> List[dict]:
    return _graph_build_direct_chat_messages(
        task_description=task_description,
        parent_chain_messages=parent_chain_messages,
        current_conversation_messages=current_conversation_messages,
        multimodal_parts=multimodal_parts,
        message_context=message_context,
    )


def get_last_user_message_text(state: AgentState) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""
    return get_message_text(messages[-1])


def get_last_user_message_parts(state: AgentState) -> list[dict]:
    messages = state.get("messages") or []
    if not messages:
        return []
    return get_message_parts(messages[-1])


def build_context_prompt(
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    current_task: str
) -> str:
    return _graph_build_context_prompt(
        parent_chain_messages=parent_chain_messages,
        current_conversation_messages=current_conversation_messages,
        current_task=current_task,
    )


def build_initial_state(
    user_message: Any,
    workspace_id: str,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None,
    agent_type: Optional[str] = None,
    is_root_graph: bool = False,
    forced_execution_mode: Optional[ExecutionMode] = None,
    plan_file: Optional[str] = None,
    plan_content: Optional[str] = None,
) -> dict:
    return {
        "messages": [user_message],
        "current_user_message_text": build_prompt_safe_text(user_message),
        "current_user_message_parts": get_message_parts(user_message) if isinstance(user_message, dict) else get_message_parts({"role": "user", "content": user_message}),
        "workspace_id": workspace_id,
        "plan": [],
        "results": [],
        "explore_result": None,
        "tool_history": [],
        "agent_type": agent_type,
        "is_root_graph": is_root_graph,
        "parent_chain_messages": parent_chain_messages or [],
        "current_conversation_messages": current_conversation_messages or [],
        "has_tool_use": False,
        "final_reply": None,
        "plan_file": plan_file,
        "plan_content": plan_content,
        "forced_execution_mode": forced_execution_mode,
        "last_tool_result": None,
        "iteration_count": 0,
        "max_iterations": MAX_DIRECT_ITERATIONS,
        "next_action": None,
        "last_tool_name": None,
        "last_tool_success": None,
        "last_tool_error": None,
        "invalid_tool_retry_count": 0,
        "todos": [],
        "current_todo_index": 0,
        "current_todo_goal": None,
        "current_todo_done_when": None,
        "current_todo_iteration_count": 0,
        "todo_max_iterations": MAX_DIRECT_ITERATIONS,
        "todo_status": None,
    }


def _load_plan_content_for_state(state: AgentState) -> tuple[Optional[str], Optional[str]]:
    existing_content = state.get("plan_content")
    existing_plan_file = state.get("plan_file")
    if existing_content:
        return existing_content, existing_plan_file

    workspace_id = state["workspace_id"]
    workspace_info = workspace_service.get_workspace_info(workspace_id)
    session_id = workspace_info.get("session_id", "default") if workspace_info else "default"
    plan_result = plan_file_service.read_plan(session_id=session_id, workspace_id=workspace_id)
    if not plan_result.get("success"):
        return None, existing_plan_file

    return plan_result.get("content"), plan_result.get("plan_file")


def _mode_name(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, ExecutionMode):
        return value.name
    if hasattr(value, "name"):
        return getattr(value, "name")
    return str(value).split(".")[-1].upper()


def check_state_v3(state: AgentState) -> Literal["analyze", "decide", "execute", "done"]:
    if state.get("pending_tools"):
        return "execute"

    if state.get("final_reply"):
        return "done"

    return "decide"


def route_after_analyze(state: dict) -> str:
    if state.get("pending_tools"):
        return "execute"
    return "decide"


def create_analyze_node(_llm_service=None, message_context=None, _settings_service=None):
    def analyze_node(state: AgentState) -> dict:
        user_message = get_last_user_message_text(state)
        current_agent_type = state.get("agent_type") or "director_agent"
        forced_execution_mode = state.get("forced_execution_mode")
        existing_execution_mode = state.get("execution_mode")

        console.step("分析节点", "入口", user_message)

        if existing_execution_mode is not None:
            mode_decision = {
                "mode": existing_execution_mode,
                "reason": f"保持已有执行模式: {_mode_name(existing_execution_mode)}",
            }
        elif forced_execution_mode is not None:
            mode_decision = {
                "mode": forced_execution_mode,
                "reason": f"使用预设执行模式: {forced_execution_mode.name}",
            }
        elif current_agent_type != "director_agent":
            mode_decision = {
                "mode": ExecutionMode.DIRECT,
                "reason": f"{current_agent_type} 使用专属 graph，默认走 DIRECT 执行",
            }
        else:
            mode_decision = {
                "mode": ExecutionMode.DIRECT,
                "reason": "director_agent 默认从 DIRECT 开始，由 agent 在需要时主动切到 PLAN",
            }

        intent_analysis = {
            "intent_type": "other",
            "summary": user_message[:100],
            "key_points": [user_message] if user_message else [],
            "complexity": "medium",
            "confidence": 0.7,
        }

        result = {
            "intent_analysis": intent_analysis,
            "execution_mode": mode_decision["mode"],
            "mode_reason": mode_decision["reason"],
            "suggested_tools": [],
            "has_tool_use": False,
            "final_reply": None,
            "pending_tools": [],
            "next_action": None,
        }

        if _should_use_native_multimodal_chat(state, _settings_service):
            result.update(_build_native_multimodal_chat_task(state))

        console.decision_box(
            route_after_analyze({'execution_mode': mode_decision['mode']}),
            f"执行模式: {result['execution_mode']}\n原因: {result['mode_reason']}"
        )

        if message_context:
            send_message = message_context.get("send_message")
            if send_message:
                state_metadata = {
                    "execution_mode": mode_decision["mode"].name,
                }
                send_message("", SegmentType.STATE_CHANGE, state_metadata)

        return result

    return analyze_node


def _build_tool_schema_prompt(tool_names: List[str]) -> str:
    return _graph_build_tool_schema_prompt(tool_names)


def _format_todo_prompt_block(todos: List[str], current_todo_index: int) -> str:
    return _graph_format_todo_prompt_block(todos, current_todo_index)


def create_decide_tool_action_node(llm_service=None, settings_service=None, message_context=None):
    def decide_tool_action_node(state: AgentState) -> dict:
        user_message = get_last_user_message_text(state)
        
        execution_mode = state.get("execution_mode")
        is_plan_mode = _mode_name(execution_mode) == "PLAN"
        
        if is_plan_mode:
            current_agent_type = "plan_agent"
            title = "决策节点"
            subtitle = "PLAN"
        else:
            current_agent_type = state.get("agent_type") or "director_agent"
            title = "决策节点"
            subtitle = "DIRECT"
        
        tool_history = state.get("tool_history", []) or []
        last_tool_result = state.get("last_tool_result")
        parent_chain_messages = state.get("parent_chain_messages", []) or []
        current_conversation_messages = state.get("current_conversation_messages", []) or []
        iteration_count = state.get("iteration_count", 0) or 0
        max_iterations = state.get("max_iterations", MAX_DIRECT_ITERATIONS) or MAX_DIRECT_ITERATIONS
        todos = state.get("todos") or []

        console.step(title, subtitle, f"第 {iteration_count + 1}/{max_iterations} 轮")

        if iteration_count >= max_iterations:
            reply = "抱歉，当前任务在限定步骤内未完成。我已经停止继续调用工具，请你细化要求或分步执行。"
            _emit_final_reply(reply, message_context)
            return {
                "next_action": {"kind": "reply", "reply": reply, "task_description": "达到最大迭代次数，向用户说明"},
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "iteration_count": iteration_count,
            }

        if iteration_count > 0 and iteration_count % CHECK_INTERVAL == 0:
            check_result = _check_loop_or_stuck(
                tool_history, 
                iteration_count, 
                llm_service,
                user_message=user_message,
                conversation_history=current_conversation_messages,
                todos=todos,
            )
            if check_result.get("action") == "stop":
                reason = check_result.get("reason", "检测到循环或卡死")
                reply = f"抱歉，检测到任务执行出现循环或卡死情况（{reason}）。我已经停止继续调用工具，请你细化要求或分步执行。"
                _emit_final_reply(reply, message_context)
                return {
                    "next_action": {"kind": "reply", "reply": reply, "task_description": f"循环检测停止: {reason}"},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                    "iteration_count": iteration_count,
                }

        if llm_service is None:
            reply = f"无法为任务自动决策下一步：{user_message}"
            _emit_final_reply(reply, message_context)
            return {
                "next_action": {"kind": "reply", "reply": reply, "task_description": user_message},
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
            }

        allowed_tools = get_allowed_tools(current_agent_type, settings_service)
        tool_schema_prompt = _build_tool_schema_prompt(allowed_tools)

        history_lines = []
        for idx, item in enumerate(tool_history[-5:], 1):
            result_text = str(item.get("result") or "")
            if len(result_text) > 500:
                result_text = result_text[:500] + "..."
            history_lines.append(f"{idx}. tool={item.get('tool')} args={item.get('args')} result={result_text}")
        history_block = "\n".join(history_lines) if history_lines else "(暂无工具执行历史)"

        last_result_block = "(无)"
        if last_tool_result:
            last_result_block = last_tool_result if len(last_tool_result) <= 1000 else last_tool_result[:1000] + "..."

        current_todo_index = state.get("current_todo_index", 0) or 0
        todo_block = _format_todo_prompt_block(todos, current_todo_index)
        todo_intro = f"\n\n{todo_block}\n\n" if todo_block else ""
        
        plan_intro = ""
        if not is_plan_mode:
            plan_content, _ = _load_plan_content_for_state(state)
            if plan_content:
                plan_file_display = "plan.md"
                plan_intro = (
                    f"\n\n当前工作区存在计划文件: {plan_file_display}\n"
                    "如果上一条历史对话提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，"
                    "那么你应主动使用 read_file 读取该 plan.md，再严格遵守该计划执行；否则不要因为计划文件存在就默认按计划执行。\n"
                )
        
        current_task = (
            f"原始用户请求: {user_message}\n\n"
            f"当前工作区ID: {state['workspace_id']}\n"
            f"已执行轮次: {iteration_count}/{max_iterations}\n"
            f"{plan_intro}\n"
            f"{tool_schema_prompt}\n"
            f"{todo_intro}"
            f"最近工具结果:\n{last_result_block}\n\n"
            f"最近工具历史:\n{history_block}\n\n"
        )
        
        if is_plan_mode:
            current_task += (
                "请只决定下一步动作，并以 JSON 形式返回：如果需要继续操作，返回一个 tool 调用；如果计划已完成，返回 kind=step_done；如果需要向用户输出回复，使用 chat 工具；如果无法继续，返回 kind=blocked。"
            )
        else:
            current_task += (
                "注意：只有当 todo 列表非空时，你才应围绕 todo 执行；如果当前没有 todo 且任务明显多步骤/阶段化，可以先使用 update_todo 写入完整 todo 列表。"
                "如果 todo 列表非空，你应继续通过 update_todo 覆盖更新完整 todo 列表和 doingIdx；如果任务拆分发生变化，也应通过 update_todo 一次性重写。"
                "默认按 DIRECT 执行；如果你在执行过程中发现任务明显复杂、多阶段、跨文件、需要先输出方案，才调用 switch_execution_mode 把模式切到 PLAN。"
                "如果上一条历史对话提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，那么你应先使用 read_file 读取该 plan.md，再严格遵守该计划执行。"
                "除非用户明确要求查看计划文件，否则不要为了展示而读取 plan.md。"
                "请只决定下一步动作，并以 JSON 形式返回：如果需要继续操作，返回一个 tool 调用；如果当前 todo 已完成，返回 kind=step_done；如果需要向用户输出最终回复，使用 chat 工具；如果无法继续，返回 kind=blocked。"
            )
        
        if is_plan_mode:
            system_prompt = PLAN_MODE_SYSTEM_PROMPT
        else:
            system_prompt = """你现在的职责是作为 branch code，围绕当前用户任务做出下一步执行决策，并在需要时调用合适的工具完成工作。

如果历史对话中上一条提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，那么你应先使用 read_file 读取该 plan.md，再严格遵守该计划执行；否则不要因为工作区里存在 plan.md 就默认按计划执行。

你必须且只能返回以下三种 JSON 结构之一，不要输出额外文本：

1. 调用工具：
{
  "kind": "tool",
  "tool_name": "工具名",
  "tool_args": {"参数名": "参数值"},
  "task_description": "这一步要做什么"
}

2. 当前 todo 已完成：
{
  "kind": "step_done"
}

3. 当前无法继续：
{
  "kind": "blocked",
  "reply": "阻塞原因"
}

规则：
1. 一次只能决定一步，不要输出多步计划
2. 如果用户的问题里提到了文件路径，且该文件存在，优先使用工具读取文件内容并根据内容决策下一步
3. kind=tool 时，tool_name 必填，tool_args 必填，task_description 必填
4. kind=tool 时，tool_name 必须来自工具协议里的工具名，tool_args 必须严格使用协议里的参数名
5. kind=blocked 时，不要返回 tool_name 或 tool_args
6. 如果任务明显复杂、多阶段、跨文件、需要先输出方案，或者用户明确要求先给方案/计划，优先调用 switch_execution_mode 把模式切到 PLAN
7. 如果当前任务是多步骤/有阶段或是任务执行过程中有不确定因素不能一口气完成的，使用 update_todo 写入完整 todo 列表
8. 如果 todo 不为空，优先围绕完整 todo 列表继续执行，并通过 update_todo 覆盖更新完整列表与 doingIdx
9. 如果任务拆分发生变化，直接用 update_todo 重写整个 todo 列表
10. 只有当前工作真的完成时，才能返回 step_done
11. 如果拿不准下一步该用什么工具或缺少必填参数，返回 blocked，不要返回不完整的 tool JSON
12. 如果发现现有工具无法解决用户的问题，例如读取二进制文件、处理特定格式文件，但你刚好没有能处理这类文件的工具时，可以使用 chat 工具向用户说明情况。
13. 当需要向用户输出最终回复或回答用户问题时，必须使用 chat 工具，不要尝试返回其他格式。
"""

        context_prompt = build_context_prompt(parent_chain_messages, current_conversation_messages, current_task)

        response = None
        try:
            response = llm_service.chat(
                messages=[{"role": "user", "content": context_prompt}],
                system_prompt=system_prompt,
            )
            console.response_box(response)
            response_text = response.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            decision_data = json.loads(response_text)
        except Exception as e:
            console.box("决策解析失败", response_text if 'response_text' in locals() else str(response) if response else "No response")
            reply = f"当前无法自动决策下一步：{e}；原始回复：{response_text if 'response_text' in locals() else (response if response else 'No response')}"
            _emit_final_reply(reply, message_context)
            return {
                "next_action": {"kind": "reply", "reply": reply, "task_description": user_message},
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
            }

        kind = decision_data.get("kind")
        if kind == "step_done":
            return {
                "todo_status": "step_done",
                "has_tool_use": False,
                "pending_tools": [],
            }
        if kind == "blocked":
            reply = decision_data.get("reply") or "当前 todo 被阻塞"
            _emit_final_reply(reply, message_context)
            return {
                "todo_status": "blocked",
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
            }

        tool_name = decision_data.get("tool_name")
        tool_args = decision_data.get("tool_args") or {}
        task_description = decision_data.get("task_description") or user_message

        if not tool_name or not is_tool_allowed(tool_name, current_agent_type, settings_service):
            console.box("无效工具决策原始回复", json.dumps(decision_data, ensure_ascii=False, indent=2))
            retry_count = (state.get("invalid_tool_retry_count", 0) or 0) + 1
            if retry_count <= 3:
                console.decision_box("decide", f"工具决策无效，使用相同提示词重试第 {retry_count}/3 次")
                return {
                    "pending_tools": [],
                    "has_tool_use": False,
                    "final_reply": None,
                    "next_action": None,
                    "invalid_tool_retry_count": retry_count,
                }

            reply = f"工具决策无效，无法继续执行：{tool_name}；原始回复：{json.dumps(decision_data, ensure_ascii=False)}"
            _emit_final_reply(reply, message_context)
            return {
                "next_action": {"kind": "reply", "reply": reply, "task_description": task_description},
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "invalid_tool_retry_count": retry_count,
            }

        pending = [{"tool": tool_name, "args": dict(tool_args)}]
        return {
            "next_action": {
                "kind": "tool",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "task_description": task_description,
            },
            "pending_tools": pending,
            "has_tool_use": True,
            "final_reply": None,
            "invalid_tool_retry_count": 0,
        }

    return decide_tool_action_node


def create_decide_next_action_node(llm_service=None, settings_service=None, message_context=None):
    return create_decide_tool_action_node(llm_service, settings_service, message_context)


def create_step_review_node(llm_service=None, message_context=None):
    def step_review_node(state: AgentState) -> dict:
        todos = state.get("todos") or []
        current_todo_index = state.get("current_todo_index", 0) or 0
        if not todos:
            return {
                "todo_status": "continue",
                "has_tool_use": False,
                "pending_tools": [],
                "todos": todos,
            }

        if current_todo_index >= len(todos):
            return {"final_reply": "任务已完成。", "has_tool_use": False}

        if state.get("last_tool_success") is False:
            return {
                "todo_status": "blocked",
                "has_tool_use": False,
                "pending_tools": [],
                "todos": todos,
            }

        return {
            "todo_status": state.get("todo_status") or "continue",
            "has_tool_use": False,
            "pending_tools": [],
            "todos": todos,
        }

    return step_review_node


def create_plan_node(llm_service=None, token_callback=None, settings_service=None, message_context=None):
    def plan_node(state: AgentState) -> dict:
        user_message = get_last_user_message_text(state)
        workspace_id = state["workspace_id"]

        console.step("规划节点", "分析节点", user_message)

        workspace_info = workspace_service.get_workspace_info(workspace_id)
        session_id = workspace_info.get("session_id", "default") if workspace_info else "default"

        if llm_service:
            system_prompt, messages = build_director_plan_messages(user_message)

            try:
                response = llm_service.chat(messages, system_prompt=system_prompt)

                console.response_box(response)

                import json
                response_text = response.strip()
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                if response_text.startswith("```"):
                    response_text = response_text[3:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
                response_text = response_text.strip()

                data = json.loads(response_text)
                raw_tasks = data.get("tasks") if isinstance(data, dict) else None
                if not raw_tasks:
                    raise ValueError("计划结果缺少 tasks")

                plan = []
                for i, task in enumerate(raw_tasks, 1):
                    plan.append({
                        "id": i,
                        "description": task.get("description") or f"步骤 {i}",
                        "goal": task.get("goal") or task.get("description") or f"完成步骤 {i}",
                        "done_when": task.get("done_when") or "该步骤目标达成",
                        "phase": task.get("phase") or "implementation",
                        "status": "pending",
                        "tool": None,
                        "args": None,
                        "result": None,
                        "feedback": None,
                    })

                console.task_list_box(plan)

            except Exception as e:
                console.warning(f"调用大模型失败: {e}，使用默认计划")
                plan = [
                    {"id": 1, "description": f"理解需求并确认工作区现状", "goal": "明确任务边界", "done_when": "已确认目标文件和工作区状态", "phase": "research", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                    {"id": 2, "description": "执行核心改动", "goal": "完成用户请求的功能", "done_when": "相关文件和行为已按要求完成", "phase": "implementation", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                    {"id": 3, "description": "验证结果", "goal": "确认结果满足要求", "done_when": "测试或检查结果符合预期", "phase": "verification", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                ]
        else:
            console.warning("LLM服务未配置，使用默认计划")
            plan = [
                {"id": 1, "description": f"理解需求并确认工作区现状", "goal": "明确任务边界", "done_when": "已确认目标文件和工作区状态", "phase": "research", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                {"id": 2, "description": "执行核心改动", "goal": "完成用户请求的功能", "done_when": "相关文件和行为已按要求完成", "phase": "implementation", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                {"id": 3, "description": "验证结果", "goal": "确认结果满足要求", "done_when": "测试或检查结果符合预期", "phase": "verification", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
            ]

        plan_content = plan_file_service.format_plan_as_markdown(user_message, plan)
        create_result = plan_file_service.create_plan(
            session_id=session_id,
            workspace_id=workspace_id,
            plan_content=plan_content,
            plan_steps=plan,
            metadata={"task_description": user_message}
        )
        plan_file_path = create_result.get("plan_file")

        console.box("计划文件已创建", plan_file_path)

        if message_context:
            send_message = message_context.get("send_message")
            if send_message:
                state_metadata = {
                    "execution_mode": "PLAN",
                    "plan_steps": len(plan),
                    "plan_file": plan_file_path,
                }
                send_message("", SegmentType.STATE_CHANGE, state_metadata)

        chat_description = f"""计划已生成并保存到 plan.md。

以下是计划内容：
{plan_content}

请向用户简要总结这个计划，并询问用户是否同意执行。"""

        console.decision_box("execute", "计划已生成，调用 chat 工具输出")
        return {
            "plan": plan,
            "plan_file": plan_file_path,
            "plan_content": plan_content,
            "final_reply": None,
            "has_tool_use": True,
            "pending_tools": [{"tool": "chat", "args": {"description": chat_description}}],
            "next_action": {
                "kind": "tool",
                "tool_name": "chat",
                "tool_args": {"description": chat_description},
                "task_description": "总结计划并询问用户",
            },
        }

    return plan_node


def _format_file_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _execute_read_file(tool_args: dict) -> dict:
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    encoding = tool_args.get("encoding", "utf-8")
    start_line = tool_args.get("start_line", 1)
    end_line = tool_args.get("end_line")
    
    console.info(f"read_file: {file_path}")
    
    try:
        if not os.path.exists(file_path):
            return {"result": None, "error": f"文件不存在: {file_path}"}
        
        if not os.path.isfile(file_path):
            return {"result": None, "error": f"路径不是文件: {file_path}"}
        
        with open(file_path, "r", encoding=encoding) as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        start_idx = max(0, start_line - 1)
        end_idx = end_line if end_line else total_lines
        
        selected_lines = lines[start_idx:end_idx]
        
        result_lines = []
        for i, line in enumerate(selected_lines, start=start_idx + 1):
            result_lines.append(f"{i:6d}\t{line.rstrip()}")
        
        content = "\n".join(result_lines)
        if end_line is None or end_line >= total_lines:
            summary = f"文件共 {total_lines} 行，已读取全部内容"
        else:
            summary = f"文件共 {total_lines} 行，已读取第 {start_line}-{end_line} 行"
        
        console.success(f"read_file 成功: {summary}")
        return {"result": f"{summary}\n\n{content}", "error": None}
    
    except UnicodeDecodeError:
        return {"result": None, "error": f"文件编码错误，无法用 {encoding} 解码"}
    except Exception as e:
        console.error(f"read_file 失败: {e}")
        return {"result": None, "error": f"读取文件失败: {str(e)}"}


def _execute_write_file(tool_args: dict) -> dict:
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    content = tool_args.get("content")
    if content is None:
        return {"result": None, "error": "缺少 content 参数"}
    
    mode = tool_args.get("mode", "write")
    encoding = tool_args.get("encoding", "utf-8")
    
    console.info(f"write_file: {file_path}, mode: {mode}")
    
    try:
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        
        write_mode = "a" if mode == "append" else "w"
        with open(file_path, write_mode, encoding=encoding) as f:
            f.write(content)
        
        action = "追加" if mode == "append" else "写入"
        console.success(f"write_file 成功: {action} {len(content)} 字符")
        return {"result": f"文件{action}成功: {file_path}", "error": None}
    
    except Exception as e:
        console.error(f"write_file 失败: {e}")
        return {"result": None, "error": f"写入文件失败: {str(e)}"}


def _execute_delete_file(tool_args: dict) -> dict:
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    console.info(f"delete_file: {file_path}")
    
    try:
        if not os.path.exists(file_path):
            return {"result": None, "error": f"路径不存在: {file_path}"}
        
        if os.path.isfile(file_path):
            os.remove(file_path)
            console.success("delete_file 成功: 已删除文件")
            return {"result": f"文件已删除: {file_path}", "error": None}
        elif os.path.isdir(file_path):
            shutil.rmtree(file_path)
            console.success("delete_file 成功: 已删除目录及其内容")
            return {"result": f"目录已删除: {file_path}", "error": None}
        else:
            return {"result": None, "error": f"未知文件类型: {file_path}"}
    
    except Exception as e:
        print(f"[ToolExec] delete_file 失败: {e}")
        return {"result": None, "error": f"删除失败: {str(e)}"}


def _execute_list_dir(tool_args: dict) -> dict:
    dir_path = tool_args.get("directory") or tool_args.get("path") or tool_args.get("dir_path")
    if not dir_path:
        return {"result": None, "error": "缺少 directory 参数"}
    
    recursive = tool_args.get("recursive", False)
    show_hidden = tool_args.get("show_hidden", False)
    
    print(f"[ToolExec] list_dir: {dir_path}, recursive: {recursive}")
    
    try:
        if not os.path.exists(dir_path):
            return {"result": None, "error": f"目录不存在: {dir_path}"}
        
        if not os.path.isdir(dir_path):
            return {"result": None, "error": f"路径不是目录: {dir_path}"}
        
        result_lines = []
        file_count = 0
        dir_count = 0
        
        if recursive:
            for root, dirs, files in os.walk(dir_path):
                if not show_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files = [f for f in files if not f.startswith(".")]
                
                rel_root = os.path.relpath(root, dir_path)
                if rel_root == ".":
                    rel_root = ""
                
                for d in sorted(dirs):
                    dir_count += 1
                    prefix = f"{rel_root}/" if rel_root else ""
                    result_lines.append(f"📁 {prefix}{d}/")
                
                for f in sorted(files):
                    file_count += 1
                    prefix = f"{rel_root}/" if rel_root else ""
                    result_lines.append(f"📄 {prefix}{f}")
        else:
            entries = os.listdir(dir_path)
            if not show_hidden:
                entries = [e for e in entries if not e.startswith(".")]
            
            for entry in sorted(entries):
                full_path = os.path.join(dir_path, entry)
                if os.path.isdir(full_path):
                    dir_count += 1
                    result_lines.append(f"📁 {entry}/")
                else:
                    file_count += 1
                    result_lines.append(f"📄 {entry}")
        
        summary = f"目录: {dir_path}\n共 {dir_count} 个目录, {file_count} 个文件"
        content = "\n".join(result_lines) if result_lines else "(空目录)"
        
        print(f"[ToolExec] list_dir 成功: {dir_count} 目录, {file_count} 文件")
        return {"result": f"{summary}\n\n{content}", "error": None}
    
    except Exception as e:
        print(f"[ToolExec] list_dir 失败: {e}")
        return {"result": None, "error": f"列出目录失败: {str(e)}"}


def _execute_create_dir(tool_args: dict) -> dict:
    dir_path = tool_args.get("directory") or tool_args.get("path") or tool_args.get("dir_path")
    if not dir_path:
        return {"result": None, "error": "缺少 directory 参数"}
    
    print(f"[ToolExec] create_dir: {dir_path}")
    
    try:
        if os.path.exists(dir_path):
            if os.path.isdir(dir_path):
                return {"result": f"目录已存在: {dir_path}", "error": None}
            else:
                return {"result": None, "error": f"路径已存在但不是目录: {dir_path}"}
        
        os.makedirs(dir_path, exist_ok=True)
        print(f"[ToolExec] create_dir 成功")
        return {"result": f"目录已创建: {dir_path}", "error": None}
    
    except Exception as e:
        print(f"[ToolExec] create_dir 失败: {e}")
        return {"result": None, "error": f"创建目录失败: {str(e)}"}


def _execute_explore_code(tool_args: dict) -> dict:
    import glob as glob_module
    import re
    
    workspace_root = tool_args.get("workspace_root", ".")
    query = tool_args.get("query", "")
    search_type = tool_args.get("search_type", "file")
    max_results = tool_args.get("max_results", 20)
    file_pattern = tool_args.get("file_pattern", "**/*.py")
    
    print(f"[ToolExec] explore_code: query={query}, type={search_type}")
    
    try:
        findings = []
        
        if search_type == "file":
            pattern = file_pattern if file_pattern else "**/*"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            for m in matches[:max_results]:
                if os.path.isfile(m):
                    rel_path = os.path.relpath(m, workspace_root)
                    findings.append({
                        "path": rel_path,
                        "type": "file",
                        "match": os.path.basename(m)
                    })
        
        elif search_type == "code":
            if not query:
                return {"result": None, "error": "code 搜索需要 query 参数"}
            
            pattern = file_pattern if file_pattern else "**/*.py"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            
            try:
                regex = re.compile(query, re.IGNORECASE)
            except re.error:
                regex = re.compile(re.escape(query), re.IGNORECASE)
            
            for file_path in matches:
                if not os.path.isfile(file_path):
                    continue
                if len(findings) >= max_results:
                    break
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                rel_path = os.path.relpath(file_path, workspace_root)
                                findings.append({
                                    "path": rel_path,
                                    "type": "code",
                                    "line": line_num,
                                    "content": line.strip()[:100]
                                })
                                if len(findings) >= max_results:
                                    break
                except Exception:
                    continue
        
        elif search_type == "structure":
            pattern = file_pattern if file_pattern else "**/*.py"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            
            structure_patterns = {
                "class": re.compile(r"^\s*class\s+(\w+)"),
                "def": re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),
                "import": re.compile(r"^\s*(?:from|import)\s+([\w.]+)"),
            }
            
            for file_path in matches:
                if not os.path.isfile(file_path):
                    continue
                if len(findings) >= max_results:
                    break
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        rel_path = os.path.relpath(file_path, workspace_root)
                        file_structures = {"path": rel_path, "classes": [], "functions": [], "imports": []}
                        
                        for line in f:
                            for struct_type, pattern in structure_patterns.items():
                                match = pattern.match(line)
                                if match:
                                    file_structures[f"{struct_type}s"].append(match.group(1))
                        
                        if any([file_structures["classes"], file_structures["functions"], file_structures["imports"]]):
                            findings.append(file_structures)
                except Exception:
                    continue
        
        else:
            return {"result": None, "error": f"不支持的搜索类型: {search_type}"}
        
        if not findings:
            return {"result": "未找到匹配结果", "error": None}
        
        result_lines = [f"探索结果 (类型: {search_type}, 共 {len(findings)} 项):\n"]
        
        for item in findings:
            if item["type"] == "file":
                result_lines.append(f"  📄 {item['path']}")
            elif item["type"] == "code":
                result_lines.append(f"  📍 {item['path']}:{item['line']}")
                result_lines.append(f"     {item['content']}")
            elif "classes" in item:
                result_lines.append(f"  📁 {item['path']}")
                if item["classes"]:
                    result_lines.append(f"     Classes: {', '.join(item['classes'][:5])}")
                if item["functions"]:
                    result_lines.append(f"     Functions: {', '.join(item['functions'][:5])}")
        
        result = "\n".join(result_lines)
        print(f"[ToolExec] explore_code 成功: {len(findings)} 项结果")
        return {"result": result, "error": None}
    
    except Exception as e:
        print(f"[ToolExec] explore_code 失败: {e}")
        return {"result": None, "error": f"探索失败: {str(e)}"}


def _execute_explore_internet(tool_args: dict) -> dict:
    query = tool_args.get("query") or tool_args.get("description") or tool_args.get("task_description")
    if not query:
        return {"result": None, "error": "缺少 query 参数"}
    
    max_results = tool_args.get("max_results", 5)
    
    print(f"[ToolExec] explore_internet: {query}, max_results: {max_results}")
    
    try:
        from duckduckgo_search import DDGS
        
        results = []
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=max_results))
        
        if not search_results:
            return {"result": "未找到相关结果", "error": None}
        
        result_lines = [f"互联网搜索结果 (查询: {query}, 共 {len(search_results)} 项):\n"]
        
        for i, item in enumerate(search_results, 1):
            title = item.get("title", "无标题")
            href = item.get("href", "")
            body = item.get("body", "")
            
            result_lines.append(f"{i}. {title}")
            if href:
                result_lines.append(f"   链接: {href}")
            if body:
                truncated_body = body[:300] + "..." if len(body) > 300 else body
                result_lines.append(f"   摘要: {truncated_body}")
            result_lines.append("")
        
        result = "\n".join(result_lines)
        print(f"[ToolExec] explore_internet 成功: {len(search_results)} 项结果")
        return {"result": result, "error": None}
    
    except ImportError:
        error_msg = "duckduckgo-search 库未安装，请运行: pip install duckduckgo-search"
        print(f"[ToolExec] explore_internet 失败: {error_msg}")
        return {"result": None, "error": error_msg}
    
    except Exception as e:
        print(f"[ToolExec] explore_internet 失败: {e}")
        return {"result": None, "error": f"搜索失败: {str(e)}"}


def _execute_call_explore_agent(tool_args: dict, llm_service=None, token_callback=None, message_context: dict = None, parent_chain_messages: List[dict] = None, current_conversation_messages: List[dict] = None) -> dict:
    task_description = tool_args.get("task_description")
    if not task_description:
        return {"result": None, "error": "缺少 task_description 参数"}

    print(f"[ToolExec] call_explore_agent: {task_description}")

    if llm_service is None:
        return {"result": None, "error": "LLM 服务未配置，无法执行子代理任务"}

    workspace_id = None
    settings_service = None
    if message_context:
        workspace_id = message_context.get("workspace_id")
        settings_service = message_context.get("settings_service")

    if not workspace_id:
        return {"result": None, "error": "缺少 workspace_id，无法切换到探索 Agent Graph"}

    try:
        from .agent_graphs import run_agent_graph
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent_graph,
                "explore_agent",
                task_description,
                workspace_id,
                llm_service,
                token_callback,
                "accumulate",
                3,
                settings_service,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
                False,
            )
            try:
                outcome = future.result(timeout=45)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": "explore_agent 子图执行超时",
                        "details": {"agent_type": "explore_agent", "timeout_seconds": 45},
                    },
                }
        if outcome.get("status") == "failed":
            exit_info = outcome.get("exit_info") or {}
            error_msg = exit_info.get("message") or exit_info.get("code") or "子代理执行失败"
            print(f"[ToolExec] call_explore_agent 失败: {error_msg}")
            return {"result": None, "error": error_msg, "outcome": outcome}
        result = outcome.get("payload") or ""
        print(f"[ToolExec] call_explore_agent 完成")
        return {"result": result, "error": None, "outcome": outcome}

    except Exception as e:
        print(f"[ToolExec] call_explore_agent 失败: {e}")
        return {"result": None, "error": f"子代理执行失败: {str(e)}"}


def _execute_call_review_agent(tool_args: dict, llm_service=None, token_callback=None, message_context: dict = None, parent_chain_messages: List[dict] = None, current_conversation_messages: List[dict] = None) -> dict:
    task_description = tool_args.get("task_description")
    if not task_description:
        return {"result": None, "error": "缺少 task_description 参数"}

    print(f"[ToolExec] call_review_agent: {task_description}")

    if llm_service is None:
        return {"result": None, "error": "LLM 服务未配置，无法执行子代理任务"}

    workspace_id = None
    settings_service = None
    if message_context:
        workspace_id = message_context.get("workspace_id")
        settings_service = message_context.get("settings_service")

    if not workspace_id:
        return {"result": None, "error": "缺少 workspace_id，无法切换到审查 Agent Graph"}

    try:
        from .agent_graphs import run_agent_graph
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent_graph,
                "review_agent",
                task_description,
                workspace_id,
                llm_service,
                token_callback,
                "accumulate",
                3,
                settings_service,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
                False,
            )
            try:
                outcome = future.result(timeout=45)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": "review_agent 子图执行超时",
                        "details": {"agent_type": "review_agent", "timeout_seconds": 45},
                    },
                }
        if outcome.get("status") == "failed":
            exit_info = outcome.get("exit_info") or {}
            error_msg = exit_info.get("message") or exit_info.get("code") or "子代理执行失败"
            print(f"[ToolExec] call_review_agent 失败: {error_msg}")
            return {"result": None, "error": error_msg, "outcome": outcome}
        result = outcome.get("payload") or ""
        print(f"[ToolExec] call_review_agent 完成")
        return {"result": result, "error": None, "outcome": outcome}

    except Exception as e:
        print(f"[ToolExec] call_review_agent 失败: {e}")
        return {"result": None, "error": f"子代理执行失败: {str(e)}"}


def _execute_list_workspace_files(workspace_id: str, workspace_service) -> dict:
    console.info(f"列出工作区文件: {workspace_id}")
    
    success, files, error_msg = workspace_service.list_files(workspace_id)
    
    if not success:
        console.error(f"列出文件失败: {error_msg}")
        return {"result": None, "error": error_msg}
    
    if not files:
        console.success("工作区为空")
        return {"result": "工作区为空，暂无文件", "error": None}
    
    result_lines = ["工作区文件列表：\n"]
    for f in files:
        icon = "📁" if f["is_dir"] else "📄"
        size_str = "" if f["is_dir"] else f" ({_format_file_size(f['size'])})"
        result_lines.append(f"  {icon} {f['path']}{size_str}")
    
    result = "\n".join(result_lines)
    console.success(f"找到 {len(files)} 个文件/目录")
    return {"result": result, "error": None}


def _execute_get_workspace_info(workspace_id: str, workspace_service) -> dict:
    console.info(f"获取工作区信息: {workspace_id}")
    
    info = workspace_service.get_workspace_info(workspace_id)
    if not info:
        console.error(f"工作区不存在: {workspace_id}")
        return {"result": None, "error": f"工作区不存在: {workspace_id}"}
    
    workspace_dir = workspace_service.get_workspace_dir(workspace_id)
    
    result_lines = [
        "工作区信息：",
        f"  ID: {info.get('id')}",
        f"  会话ID: {info.get('session_id')}",
        f"  状态: {info.get('status')}",
        f"  路径: {workspace_dir}",
    ]
    
    if workspace_dir and os.path.exists(workspace_dir):
        total_size = 0
        file_count = 0
        dir_count = 0
        for root, dirs, files in os.walk(workspace_dir):
            dir_count += len(dirs)
            for f in files:
                file_count += 1
                total_size += os.path.getsize(os.path.join(root, f))
        result_lines.extend([
            f"  文件数: {file_count}",
            f"  目录数: {dir_count}",
            f"  总大小: {_format_file_size(total_size)}",
        ])
    
    result = "\n".join(result_lines)
    console.success("获取工作区信息成功")
    return {"result": result, "error": None}


def _execute_search_files(tool_args: dict, workspace_id: str, workspace_service) -> dict:
    pattern = tool_args.get("pattern", "*")
    console.info(f"搜索文件: pattern={pattern}, workspace={workspace_id}")
    
    workspace_dir = workspace_service.get_workspace_dir(workspace_id)
    if not workspace_dir:
        console.error(f"工作区不存在: {workspace_id}")
        return {"result": None, "error": f"工作区不存在: {workspace_id}"}
    
    if not os.path.exists(workspace_dir):
        console.success("工作区目录不存在，无文件")
        return {"result": "工作区为空", "error": None}
    
    matches = []
    for root, dirs, files in os.walk(workspace_dir):
        for filename in files:
            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, workspace_dir)
                matches.append({
                    "name": filename,
                    "path": rel_path.replace("\\", "/"),
                    "size": os.path.getsize(full_path),
                })
        for dirname in dirs:
            if fnmatch.fnmatch(dirname.lower(), pattern.lower()):
                full_path = os.path.join(root, dirname)
                rel_path = os.path.relpath(full_path, workspace_dir)
                matches.append({
                    "name": dirname,
                    "path": rel_path.replace("\\", "/"),
                    "is_dir": True,
                })
    
    if not matches:
        console.success(f"未找到匹配 '{pattern}' 的文件")
        return {"result": f"未找到匹配 '{pattern}' 的文件", "error": None}
    
    result_lines = [f"找到 {len(matches)} 个匹配 '{pattern}' 的结果：\n"]
    for m in matches:
        icon = "📁" if m.get("is_dir") else "📄"
        size_str = "" if m.get("is_dir") else f" ({_format_file_size(m['size'])})"
        result_lines.append(f"  {icon} {m['path']}{size_str}")
    
    result = "\n".join(result_lines)
    console.success(f"找到 {len(matches)} 个匹配项")
    return {"result": result, "error": None}


def _execute_workspace_tool(tool_name: str, tool_args: dict, workspace_id: str, workspace_service) -> dict:
    console.section(f"Workspace 工具: {tool_name}")
    
    if workspace_service is None:
        workspace_service = WorkspaceService()
    
    if tool_name == "list_workspace_files":
        return _execute_list_workspace_files(workspace_id, workspace_service)
    elif tool_name == "get_workspace_info":
        return _execute_get_workspace_info(workspace_id, workspace_service)
    elif tool_name == "search_files":
        return _execute_search_files(tool_args, workspace_id, workspace_service)
    else:
        return {"result": None, "error": f"未知的 workspace 工具: {tool_name}"}


def _execute_thinking_tool_direct(
    task_description: str,
    llm_service,
    message_context: dict,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
) -> dict:
    if not llm_service:
        result = f"思考任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    console.info("调用 LLM 进行思考...")
    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", SegmentType.THINKING_START, {
            "task_description": task_description,
            "is_start": True
        })

    try:
        full_prompt = build_context_prompt(
            parent_chain_messages,
            current_conversation_messages,
            f"请思考并执行当前任务: {task_description}"
        )
        messages = [{"role": "user", "content": full_prompt}]

        def thinking_token_callback(token: str):
            if send_message:
                send_message(token, SegmentType.THINKING_DELTA, {
                    "task_description": task_description,
                    "is_delta": True
                })

        result = ""
        for chunk in llm_service.chat_stream(messages, THINK_SYSTEM_PROMPT, thinking_token_callback):
            result += chunk

        console.success("思考完成")

        if send_message:
            send_message("", SegmentType.THINKING_END, {
                "task_description": task_description,
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", SegmentType.THINKING_END, {
                "task_description": task_description,
                "is_end": True,
                "error": str(e)
            })
        return {"result": f"思考失败: {e}", "error": str(e)}


def _execute_chat_tool_direct(
    task_description: str,
    llm_service,
    message_context: dict,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    tool_args: Optional[dict] = None,
    multimodal_parts: Optional[List[dict]] = None,
) -> dict:
    if not llm_service:
        result = f"回复任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    next_task = task_description
    if tool_args:
        next_task = tool_args.get("next_task") or tool_args.get("description") or task_description

    console.info(f"调用 LLM 进行对话回复，任务: {next_task}")
    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", SegmentType.CHAT_START, {
            "task_description": next_task,
            "is_start": True
        })

    try:
        messages = _build_direct_chat_messages(
            task_description=next_task,
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            multimodal_parts=multimodal_parts,
            message_context=message_context,
        )

        def chat_token_callback(token: str):
            if send_message:
                send_message(token, SegmentType.CHAT_DELTA, {
                    "task_description": next_task,
                    "is_delta": True
                })

        result = ""
        chat_system_prompt = _build_chat_system_prompt(message_context.get("settings_service") if message_context else None)
        for chunk in llm_service.chat_stream(messages, chat_system_prompt, chat_token_callback):
            result += chunk

        console.success("对话回复完成")

        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": next_task,
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": next_task,
                "is_end": True,
                "error": str(e)
            })
        return {"result": f"对话回复失败: {e}", "error": str(e)}


def create_execute_node(llm_service=None, token_callback=None, settings_service=None, message_context=None):
    def execute_node(state: AgentState) -> dict:
        if message_context:
            cancel_check = message_context.get("cancel_check")
            if cancel_check:
                cancel_check()

        pending_tools = state.get("pending_tools", [])
        current_agent_type = state.get("agent_type") or "director_agent"
        parent_chain_messages = state.get("parent_chain_messages", [])
        current_conversation_messages = state.get("current_conversation_messages", [])
        workspace_id = state["workspace_id"]
        execution_mode = state.get("execution_mode")

        if pending_tools:
            tool_name = pending_tools[0].get("tool")
            tool_args = pending_tools[0].get("args", {})
            task_description = (
                (state.get("next_action") or {}).get("task_description")
                or tool_args.get("description", "")
            )

            console.step("执行节点", "分析节点", f"执行工具: {tool_name}")

            console.box("执行工具", {
                "工具名称": tool_name,
                "工具参数": tool_args
            })

            if tool_name == "chat":
                tool_result = _execute_chat_tool_direct(
                    task_description=task_description,
                    llm_service=llm_service,
                    message_context=message_context,
                    parent_chain_messages=parent_chain_messages,
                    current_conversation_messages=current_conversation_messages,
                    tool_args=tool_args,
                    multimodal_parts=tool_args.get("multimodal_parts"),
                )
            else:
                enhanced_message_context = dict(message_context) if message_context else {}
                enhanced_message_context["parent_chain_messages"] = parent_chain_messages
                enhanced_message_context["current_conversation_messages"] = current_conversation_messages
                tool_result = run_tool_execution(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    workspace_id=workspace_id,
                    previous_calls=state.get("tool_history", []),
                    workspace_service=workspace_service,
                    llm_service=llm_service,
                    token_callback=token_callback,
                    task_description=task_description,
                    previous_results=[item.get("result") for item in state.get("tool_history", []) if item.get("result")],
                    agent_type=current_agent_type,
                    settings_service=settings_service,
                    message_context=enhanced_message_context,
                )

            result_str = str(tool_result.get("result", "")) if tool_result.get("result") is not None else ""
            if len(result_str) > 4000:
                result_str = result_str[:4000] + "..."
            console.box("工具执行结果", result_str[:200])

            new_tool_history = state.get("tool_history", []) + [{
                "tool": tool_name,
                "args": tool_args,
                "result": tool_result.get("result")
            }]

            new_current_conv_msgs = list(current_conversation_messages)
            tool_error = tool_result.get("error")
            content = f"[工具执行: {tool_name}]\n结果: {result_str[:1000]}"
            if tool_error:
                content += f"\n错误: {tool_error}"
            new_current_conv_msgs.append({
                "role": "assistant",
                "content": content
            })

            tool_success = tool_result.get("error") is None

            if _mode_name(execution_mode) == "DIRECT" and tool_name != "chat":
                direct_update = {
                    "pending_tools": [],
                    "tool_history": new_tool_history,
                    "current_conversation_messages": new_current_conv_msgs,
                    "has_tool_use": False,
                    "last_tool_result": result_str,
                    "last_tool_name": tool_name,
                    "last_tool_success": tool_success,
                    "last_tool_error": tool_error,
                    "iteration_count": (state.get("iteration_count", 0) or 0) + 1,
                    "current_todo_iteration_count": (state.get("current_todo_iteration_count", 0) or 0) + 1,
                    "todo_status": "in_progress",
                    "next_action": None,
                }
                if tool_success and tool_name == "update_todo":
                    next_todos = tool_result.get("todos") or []
                    next_doing_idx = tool_result.get("doingIdx", 0)
                    direct_update.update({
                        "todos": next_todos,
                        "current_todo_index": next_doing_idx,
                        "current_todo_goal": None,
                        "current_todo_done_when": None,
                        "iteration_count": 0,
                        "current_todo_iteration_count": 0,
                        "todo_status": "pending",
                    })
                if tool_success and tool_name == "switch_execution_mode":
                    mode_value = tool_result.get("execution_mode")
                    if mode_value == "PLAN":
                        direct_update.update({
                            "execution_mode": ExecutionMode.PLAN,
                            "mode_reason": tool_result.get("mode_reason") or "agent 主动切换到 PLAN",
                            "pending_tools": [],
                            "has_tool_use": False,
                            "next_action": {
                                "kind": "enter_plan",
                                "task_description": tool_result.get("mode_reason") or "切换到 PLAN",
                            },
                        })
                    elif mode_value == "DIRECT":
                        direct_update.update({
                            "execution_mode": ExecutionMode.DIRECT,
                            "mode_reason": tool_result.get("mode_reason") or "agent 维持 DIRECT",
                        })
                return direct_update

            has_more_tools = len(pending_tools) > 1
            is_chat_tool = tool_name == "chat"

            if is_chat_tool:
                console.decision_box("done", "工具输出最终回复，结束循环")
                return {
                    "pending_tools": pending_tools[1:],
                    "tool_history": new_tool_history,
                    "current_conversation_messages": new_current_conv_msgs,
                    "has_tool_use": False,
                    "final_reply": result_str,
                    "last_tool_result": result_str,
                    "last_tool_name": tool_name,
                    "last_tool_success": tool_success,
                    "last_tool_error": tool_error,
                    "next_action": None,
                }

            console.decision_box("execute" if has_more_tools else "analyze", "继续执行或分析")

            return {
                "pending_tools": pending_tools[1:],
                "tool_history": new_tool_history,
                "current_conversation_messages": new_current_conv_msgs,
                "has_tool_use": has_more_tools,
                "last_tool_result": result_str,
                "last_tool_name": tool_name,
                "last_tool_success": tool_success,
                "last_tool_error": tool_error,
            }

        console.step("执行节点", "无", "没有任务可执行")
        console.decision_box("done", "没有任务可执行，执行完成")

        return {
            "pending_tools": [],
            "in_plan_mode": False,
            "execution_mode": None,
            "has_tool_use": False
        }
    
    return execute_node


def route_after_todo_review(_state: AgentState) -> str:
    return "decide"


def route_after_execute(state: AgentState) -> str:
    if state.get("final_reply"):
        return "done"

    next_action = state.get("next_action") or {}
    if next_action.get("kind") == "enter_plan":
        return "analyze"

    if state.get("pending_tools"):
        return "execute"
    if _mode_name(state.get("execution_mode")) == "DIRECT" and not state.get("pending_tools"):
        return "todo_review"
    return check_state_v3(state)


def create_orchestrator_graph_v3(llm_service=None, token_callback=None, memory_mode: str = "accumulate", window_size: int = 3, settings_service=None, message_context=None):
    graph = StateGraph(AgentState)

    graph.add_node("analyze", create_analyze_node(llm_service, message_context, settings_service))
    graph.add_node("decide", create_decide_next_action_node(llm_service, settings_service, message_context))
    graph.add_node("todo_review", create_step_review_node(llm_service, message_context))
    graph.add_node("execute", create_execute_node(llm_service, token_callback, settings_service, message_context))

    graph.set_entry_point("analyze")

    graph.add_conditional_edges("analyze", route_after_analyze, {
        "decide": "decide",
        "execute": "execute",
        "done": END
    })

    graph.add_conditional_edges("decide", check_state_v3, {
        "analyze": "analyze",
        "decide": "decide",
        "execute": "execute",
        "done": END
    })

    graph.add_conditional_edges("execute", route_after_execute, {
        "analyze": "analyze",
        "decide": "decide",
        "todo_review": "todo_review",
        "execute": "execute",
        "done": END
    })

    graph.add_conditional_edges("todo_review", route_after_todo_review, {
        "decide": "decide",
    })

    return graph.compile()


def run_graph_v3(
    user_message: Any,
    workspace_id: str,
    llm_service=None,
    token_callback=None,
    memory_mode: str = "accumulate",
    window_size: int = 3,
    settings_service=None,
    message_context: dict = None,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None
) -> dict:
    print("\n" + "="*60)
    print("[Director Agent] 块类型驱动循环 + Plan/Execute 分离")
    print(f"[Director Agent] 记忆模式: {memory_mode}, 窗口大小: {window_size}")
    print("="*60)

    initial_state = build_initial_state(
        user_message=user_message,
        workspace_id=workspace_id,
        parent_chain_messages=parent_chain_messages,
        current_conversation_messages=current_conversation_messages,
        is_root_graph=True,
    )

    graph = create_orchestrator_graph_v3(llm_service, token_callback, memory_mode, window_size, settings_service, message_context)
    final_state = graph.invoke(initial_state)

    print("\n" + "="*60)
    print("[Director Agent] 主编排图执行完成")
    print("="*60)

    return final_state


run_graph_v2 = run_graph_v3
create_orchestrator_graph_v2 = create_orchestrator_graph_v3
check_state_v2 = check_state_v3
