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

from .decision.complexity_analyzer import ExecutionMode, analyze_task_complexity, evaluate_task_complexity
from ..state import AgentState
from .subgraphs.tool_registry import (
    is_tool_allowed, get_allowed_tools, _write_tool_event
)
from .subgraphs.tool_executor import run_tool_execution
from service.session_service.canonical import SegmentType
from service.agent_service.service.plan_file_service import plan_file_service
from service.agent_service.service.workspace_service import WorkspaceService
from core.logging import console
from singleton import get_workspace_service

MAX_REPLAN_COUNT = 3
MAX_MESSAGES = 10
MAX_DIRECT_ITERATIONS = 8

workspace_service = get_workspace_service()


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


THINK_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前正在执行一个任务计划中的某个步骤。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请针对当前任务进行思考：
1. 分析任务目标
2. 结合之前的执行结果（如果有）
3. 给出你的思考过程和结论

请简洁清晰地回答，不要过于冗长。"""

CHAT_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前需要向用户输出回复。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请直接向用户输出回复内容：
- 语言简洁清晰
- 直接回答用户问题
- 不要输出思考过程，只输出最终回复
- 使用友好、专业的语气"""


def build_context_prompt(
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    current_task: str
) -> str:
    prompt_parts = []
    
    if parent_chain_messages:
        prompt_parts.append("[历史对话]")
        for msg in parent_chain_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")
        prompt_parts.append("")
    
    if current_conversation_messages:
        prompt_parts.append("[当前对话内历史]")
        for msg in current_conversation_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")
        prompt_parts.append("")
    
    prompt_parts.append("[当前任务]")
    prompt_parts.append(current_task)
    
    return "\n".join(prompt_parts)


def build_initial_state(
    user_message: str,
    workspace_id: str,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None,
    agent_type: Optional[str] = None,
    is_root_graph: bool = False,
) -> dict:
    return {
        "messages": [user_message],
        "workspace_id": workspace_id,
        "plan": [],
        "current_step": 0,
        "results": [],
        "plan_failed": False,
        "explore_result": None,
        "tool_history": [],
        "replan_count": 0,
        "agent_type": agent_type,
        "is_root_graph": is_root_graph,
        "parent_chain_messages": parent_chain_messages or [],
        "current_conversation_messages": current_conversation_messages or [],
        "has_tool_use": False,
        "final_reply": None,
        "plan_file": None,
        "last_tool_result": None,
        "iteration_count": 0,
        "max_iterations": MAX_DIRECT_ITERATIONS,
        "next_action": None,
        "last_tool_name": None,
        "last_tool_success": None,
        "last_tool_error": None,
        "current_step_goal": None,
        "current_step_done_when": None,
        "current_step_iteration_count": 0,
        "step_max_iterations": MAX_DIRECT_ITERATIONS,
        "step_status": None,
        "replan_reason": None,
        "todos": [],
        "current_todo_index": 0,
        "current_todo_goal": None,
        "current_todo_done_when": None,
        "current_todo_iteration_count": 0,
        "todo_max_iterations": MAX_DIRECT_ITERATIONS,
        "todo_status": None,
    }


def check_state_v3(state: AgentState) -> Literal["analyze", "decide", "execute", "plan", "done"]:
    if "execution_mode" not in state:
        return "analyze"

    if state.get("pending_tools"):
        return "execute"

    execution_mode = state.get("execution_mode")
    if execution_mode is None:
        return "done"

    if execution_mode == ExecutionMode.PLAN:
        if state.get("final_reply"):
            return "done"
        return "plan"

    if execution_mode == ExecutionMode.DIRECT:
        if state.get("final_reply"):
            return "done"
        if state.get("pending_tools"):
            return "execute"
        return "decide"

    if state.get("has_tool_use", False):
        return "execute"

    if state.get("final_reply"):
        return "done"

    return "done"


def route_after_analyze(state: dict) -> str:
    mode = state.get("execution_mode")
    if mode == ExecutionMode.PLAN:
        return "plan"
    elif mode == ExecutionMode.DIRECT:
        return "decide"
    return "done"


def create_analyze_node(llm_service=None, message_context=None, settings_service=None):
    def analyze_node(state: AgentState) -> dict:
        user_message = state["messages"][-1] if state["messages"] else ""
        current_agent_type = state.get("agent_type") or "director_agent"

        console.step("分析节点", "入口", user_message)
        
        if llm_service:
            system_prompt = """你是一个任务分析专家。请分析用户任务的复杂度，并决定执行模式。

执行模式选项：
1. DIRECT - 直接执行：适用于简单任务，如读取文件、查询信息等
2. PLAN - 规划模式：适用于复杂开发任务，需要多步骤规划（仅 director_agent 可用）

请以JSON格式返回分析结果：
{
    "complexity": "simple/medium/complex",
    "intent_type": "develop/explore/review/question/debug/refactor/other",
    "execution_mode": "DIRECT/PLAN",
    "reason": "选择该模式的原因"
}

只返回JSON，不要其他内容。"""
            
            parent_chain_messages = state.get("parent_chain_messages", [])
            current_conversation_messages = state.get("current_conversation_messages", [])
            
            full_prompt = build_context_prompt(
                parent_chain_messages,
                current_conversation_messages,
                f"user: {user_message}"
            )
            messages = [{"role": "user", "content": full_prompt}]
            
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
                
                analysis_result = json.loads(response_text)
                
                mode_str = analysis_result.get("execution_mode", "DIRECT")
                if mode_str == "SUBAGENT":
                    mode_str = "DIRECT"
                execution_mode = ExecutionMode[mode_str]

                mode_decision = {
                    "mode": execution_mode,
                    "reason": analysis_result.get("reason", ""),
                }

                if current_agent_type != "director_agent":
                    mode_decision["mode"] = ExecutionMode.DIRECT
                    mode_decision["reason"] = f"{current_agent_type} 使用专属 graph，固定走 DIRECT 执行"

                intent_analysis = {
                    "intent_type": analysis_result.get("intent_type", "other"),
                    "summary": user_message[:100],
                    "key_points": [user_message],
                    "complexity": analysis_result.get("complexity", "medium"),
                    "confidence": 0.9
                }
                
            except Exception as e:
                console.warning(f"调用大模型失败: {e}，使用默认逻辑")
                complexity = evaluate_task_complexity(user_message)
                intent_analysis = {
                    "intent_type": "other",
                    "summary": user_message[:100],
                    "key_points": [user_message],
                    "complexity": complexity,
                    "confidence": 0.7
                }
                mode_decision = analyze_task_complexity(user_message, intent_analysis)
        else:
            complexity = evaluate_task_complexity(user_message)
            intent_analysis = {
                "intent_type": "other",
                "summary": user_message[:100],
                "key_points": [user_message],
                "complexity": complexity,
                "confidence": 0.7
            }
            mode_decision = analyze_task_complexity(user_message, intent_analysis)
        
        console.decision_box(
            route_after_analyze({'execution_mode': mode_decision['mode']}),
            f"执行模式: {mode_decision['mode']}\n原因: {mode_decision['reason']}"
        )
        
        result = {
            "intent_analysis": intent_analysis,
            "execution_mode": mode_decision["mode"],
            "mode_reason": mode_decision["reason"],
            "suggested_tools": [],
            "has_tool_use": mode_decision["mode"] == ExecutionMode.DIRECT,
            "final_reply": None,
            "pending_tools": [],
            "next_action": None,
        }

        if mode_decision["mode"] == ExecutionMode.DIRECT and current_agent_type != "director_agent":
            suggested_tools = []
            if current_agent_type == "explore_agent":
                suggested_tools = ["thinking", "chat"]
            elif current_agent_type == "review_agent":
                suggested_tools = ["thinking", "chat"]
            result["pending_tools"] = [
                {"tool": tool, "args": {"description": user_message}}
                for tool in suggested_tools
            ]
        
        if message_context:
            send_message = message_context.get("send_message")
            if send_message:
                from service.session_service.canonical import MessageBuilder
                state_metadata = {
                    "execution_mode": mode_decision["mode"].name,
                }
                state_msg = MessageBuilder.state_change(
                    message_id=message_context.get("message_id", ""),
                    conversation_id=message_context.get("conversation_id", ""),
                    session_id=message_context.get("session_id", ""),
                    workspace_id=message_context.get("workspace_id", ""),
                    metadata=state_metadata
                )
                send_message("", SegmentType.STATE_CHANGE, state_metadata)
        
        return result
    
    return analyze_node


def _build_tool_schema_prompt(tool_names: List[str]) -> str:
    from service.agent_service.tools import ALL_TOOLS

    schema_lines = ["工具参数协议（必须严格使用这些参数名）："]
    for tool_name in tool_names:
        tool_meta = ALL_TOOLS.get(tool_name)
        if not tool_meta:
            continue
        params = tool_meta.get("params", "")
        if params:
            schema_lines.append(f"- {tool_name}: {params}")
        else:
            schema_lines.append(f"- {tool_name}: 不需要参数")
    return "\n".join(schema_lines)


def _format_todo_prompt_block(todos: List[dict], current_todo_index: int) -> str:
    if not todos:
        return ""

    lines = ["当前 TODO 列表（完整状态）:"]
    for idx, todo in enumerate(todos):
        marker = "<= 当前执行项" if idx == current_todo_index else ""
        lines.append(
            f"- [{todo.get('id')}] status={todo.get('status', 'pending')} desc={todo.get('description', '')} goal={todo.get('goal', '')} done_when={todo.get('done_when', '')} result={todo.get('result', '')} {marker}".rstrip()
        )
    lines.append("如果任务明显是多步骤、阶段化，或执行中发现当前任务过大/过难，应使用 todo 工具创建、拆分、更新任务；如果任务本身是单步骤且简单，则不要使用 todo 工具。")
    return "\n".join(lines)


def create_decide_tool_action_node(scope: Literal["direct", "plan_step"], llm_service=None, settings_service=None, message_context=None):
    def decide_tool_action_node(state: AgentState) -> dict:
        user_message = state["messages"][-1] if state["messages"] else ""
        current_agent_type = state.get("agent_type") or "director_agent"
        tool_history = state.get("tool_history", []) or []
        last_tool_result = state.get("last_tool_result")
        parent_chain_messages = state.get("parent_chain_messages", []) or []
        current_conversation_messages = state.get("current_conversation_messages", []) or []

        if scope == "direct":
            iteration_count = state.get("iteration_count", 0) or 0
            max_iterations = state.get("max_iterations", MAX_DIRECT_ITERATIONS) or MAX_DIRECT_ITERATIONS
            title = "决策节点"
            subtitle = "DIRECT"
        else:
            iteration_count = state.get("current_step_iteration_count", 0) or 0
            max_iterations = state.get("step_max_iterations", MAX_DIRECT_ITERATIONS) or MAX_DIRECT_ITERATIONS
            title = "计划步骤决策"
            subtitle = f"Step {state.get('current_step', 0) + 1}"

        console.step(title, subtitle, f"第 {iteration_count + 1}/{max_iterations} 轮")

        if iteration_count >= max_iterations:
            if scope == "direct":
                reply = "抱歉，当前任务在限定步骤内未完成。我已经停止继续调用工具，请你细化要求或分步执行。"
                _emit_final_reply(reply, message_context)
                return {
                    "next_action": {"kind": "reply", "reply": reply, "task_description": "达到最大迭代次数，向用户说明"},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                    "iteration_count": iteration_count,
                }
            return {
                "step_status": "blocked",
                "replan_reason": "步骤超过最大执行轮次",
                "has_tool_use": False,
                "pending_tools": [],
            }

        if llm_service is None:
            reply = f"无法为任务自动决策下一步：{user_message}"
            if scope == "direct":
                _emit_final_reply(reply, message_context)
                return {
                    "next_action": {"kind": "reply", "reply": reply, "task_description": user_message},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                }
            return {
                "step_status": "blocked",
                "replan_reason": reply,
                "has_tool_use": False,
                "pending_tools": [],
            }

        allowed_tools = [tool for tool in get_allowed_tools(current_agent_type, settings_service) if tool != "chat"]
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

        tool_schema_prompt = _build_tool_schema_prompt(allowed_tools)

        if scope == "plan_step":
            plan = state.get("plan") or []
            current_step = state.get("current_step", 0)
            step = plan[current_step] if current_step < len(plan) else {}
            current_task = (
                f"原始用户请求: {user_message}\n\n"
                f"当前计划步骤: {step.get('description', '')}\n"
                f"步骤目标: {state.get('current_step_goal') or step.get('goal') or step.get('description') or ''}\n"
                f"步骤完成条件: {state.get('current_step_done_when') or step.get('done_when') or ''}\n"
                f"当前步骤轮次: {iteration_count}/{max_iterations}\n"
                f"工作区ID: {state['workspace_id']}\n\n"
                f"{tool_schema_prompt}\n\n"
                f"最近工具结果:\n{last_result_block}\n\n"
                f"最近工具历史:\n{history_block}\n\n"
                "请仅决定当前步骤的下一步，并以 JSON 形式返回。"
                "如果当前步骤还未完成，返回 kind=tool。"
                "如果当前步骤已完成，返回 kind=step_done。"
                "如果当前步骤无法继续，返回 kind=blocked，并在 reply 里说明原因。"
            )
            system_prompt = """你是 PLAN 模式中的步骤执行决策器。

请严格输出 JSON：
- kind=tool: 当前步骤下一步执行一个工具
- kind=step_done: 当前步骤已完成，准备进入下一步
- kind=blocked: 当前步骤无法继续，需要重规划或终止

要求：
1. 一次只决定一步
2. 不要直接结束整个任务，不要输出最终用户回复
3. tool_name 必须来自允许列表
4. 如果最近工具报错，优先修正或返回 blocked，不要假装完成
5. 只有当前步骤目标满足时，才能返回 step_done
"""
        else:
            todos = state.get("todos") or []
            current_todo_index = state.get("current_todo_index", 0) or 0
            current_todo = todos[current_todo_index] if current_todo_index < len(todos) else {}
            todo_block = _format_todo_prompt_block(todos, current_todo_index)
            todo_intro = ""
            if todo_block:
                todo_intro = f"\n\n{todo_block}\n\n"
            current_task = (
                f"原始用户请求: {user_message}\n\n"
                f"当前工作区ID: {state['workspace_id']}\n"
                f"已执行轮次: {iteration_count}/{max_iterations}\n\n"
                f"{tool_schema_prompt}\n"
                f"{todo_intro}"
                f"最近工具结果:\n{last_result_block}\n\n"
                f"最近工具历史:\n{history_block}\n\n"
                "注意：只有当 todo 列表非空时，你才应围绕 todo 执行；如果当前没有 todo 且任务明显多步骤/阶段化，可以先使用 todo_add 建立任务列表。"
                "如果 todo 列表非空，你应优先通过 todo_update 维护任务状态；如果发现当前任务过大，可以用 todo_add/todo_delete 重排或拆分。"
                "除非用户明确要求查看计划文件，否则不要读取 plan.md。"
                "请只决定下一步动作，并以 JSON 形式返回：如果需要继续操作，返回一个 tool 调用；如果当前 todo 已完成，返回 kind=step_done；如果全部 todo 都已完成，返回 kind=reply；如果无法继续，返回 kind=blocked。"
                "不要输出 chat 工具；最终回复请直接放在 reply 字段。"
            )
            system_prompt = """你现在的职责是作为 branch code，围绕当前用户任务做出下一步执行决策，并在需要时调用合适的工具完成工作。

当任务明显是多步骤、存在阶段划分，或者执行中发现当前任务过大/过难时，你应该使用 todo 工具维护任务列表，而不是硬撑着一次做完。

请严格输出 JSON 结构化结果：
- kind=tool: 表示下一步执行一个工具
- kind=step_done: 表示当前 todo 已完成，准备进入下一个 todo
- kind=reply: 表示所有工作都已完成，直接返回给用户的最终回复
- kind=blocked: 表示当前工作无法继续

规则：
1. 一次只能决定一步，不要输出多步计划
2. 调用工具时参考工具列表里的工具名和工具参数
3. 如果当前任务是多步骤/有阶段或是任务执行过程中有不确定因素不能一口气完成的，使用todo系列工具
4. 如果 todo 不为空，优先围绕完整 todo 列表继续执行，并通过 todo_update 维护状态
5. 如果发现某个 todo 过大，允许新增更细的 todo 并删除或重置原 todo
6. 只有当前工作真的完成时，才能返回 step_done；只有所有工作都完成时，才能返回 reply
"""

        context_prompt = build_context_prompt(parent_chain_messages, current_conversation_messages, current_task)

        try:
            response = llm_service.chat(
                messages=[{"role": "user", "content": context_prompt}],
                system_prompt=system_prompt,
            )
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
            if scope == "direct":
                reply = f"当前无法自动决策下一步：{e}"
                _emit_final_reply(reply, message_context)
                return {
                    "next_action": {"kind": "reply", "reply": reply, "task_description": user_message},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                }
            return {
                "step_status": "blocked",
                "replan_reason": str(e),
                "has_tool_use": False,
                "pending_tools": [],
            }

        kind = decision_data.get("kind")
        if scope == "plan_step":
            if kind == "step_done":
                return {"step_status": "step_done", "has_tool_use": False, "pending_tools": []}
            if kind == "blocked":
                return {
                    "step_status": "blocked",
                    "replan_reason": decision_data.get("reply") or "步骤被阻塞",
                    "has_tool_use": False,
                    "pending_tools": [],
                }
        else:
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
                    "replan_reason": reply,
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                }
            if kind == "reply":
                reply = decision_data.get("reply") or "任务已完成。"
                _emit_final_reply(reply, message_context)
                return {
                    "next_action": {
                        "kind": "reply",
                        "reply": reply,
                        "task_description": decision_data.get("task_description") or "向用户输出最终回复",
                    },
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                }

        tool_name = decision_data.get("tool_name")
        tool_args = decision_data.get("tool_args") or {}
        task_description = decision_data.get("task_description") or user_message
        if scope == "plan_step":
            task_description = decision_data.get("task_description") or (state.get("current_step_goal") or task_description)

        if not tool_name or not is_tool_allowed(tool_name, current_agent_type, settings_service):
            if scope == "direct":
                reply = f"工具决策无效，无法继续执行：{tool_name}"
                _emit_final_reply(reply, message_context)
                return {
                    "next_action": {"kind": "reply", "reply": reply, "task_description": task_description},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                }
            return {
                "step_status": "blocked",
                "replan_reason": f"步骤决策工具无效: {tool_name}",
                "has_tool_use": False,
                "pending_tools": [],
            }

        pending = [{"tool": tool_name, "args": dict(tool_args)}]
        if scope == "plan_step":
            return {
                "step_status": "in_progress",
                "pending_tools": pending,
                "has_tool_use": True,
            }
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
        }

    return decide_tool_action_node


def create_decide_next_action_node(llm_service=None, settings_service=None, message_context=None):
    return create_decide_tool_action_node("direct", llm_service, settings_service, message_context)


def create_plan_step_decide_node(llm_service=None, settings_service=None, message_context=None):
    return create_decide_tool_action_node("plan_step", llm_service, settings_service, message_context)


def create_step_review_node(llm_service=None, message_context=None):
    def step_review_node(state: AgentState) -> dict:
        todos = state.get("todos") or []
        current_todo_index = state.get("current_todo_index", 0) or 0
        if current_todo_index >= len(todos):
            return {"final_reply": "任务已完成。", "has_tool_use": False}

        if state.get("last_tool_success") is False:
            return {
                "todo_status": "blocked",
                "replan_reason": state.get("last_tool_error") or "todo 执行失败",
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


def _parse_todos_from_plan_markdown(plan_content: str) -> List[dict]:
    todos = []
    current = None
    for raw_line in plan_content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        task_match = re.match(r"^(\d+)\.\s+\*\*(.+?)\*\*$", line)
        if task_match:
            if current:
                todos.append(current)
            current = {
                "id": int(task_match.group(1)),
                "description": task_match.group(2).strip(),
                "goal": None,
                "done_when": None,
                "status": "pending",
                "result": None,
                "attempt_count": 0,
            }
            continue
        if current and line.startswith("- 目标:"):
            current["goal"] = line.split(":", 1)[1].strip().strip("`")
            continue
        if current and line.startswith("- 完成条件:"):
            current["done_when"] = line.split(":", 1)[1].strip().strip("`")
            continue
    if current:
        todos.append(current)
    return todos


def _hydrate_todos_from_plan(plan_content: str, workspace_id: str) -> List[dict]:
    from service.agent_service.tools.todo_tools import TodoList

    todos = _parse_todos_from_plan_markdown(plan_content)
    todo_store = TodoList(workspace_id, base_dir=workspace_service.base_dir)
    todo_store.clear_all()
    for item in todos:
        todo_store.add_task(description=item["description"], priority="medium")
    return todos


def create_plan_node(llm_service=None, token_callback=None, settings_service=None, message_context=None):
    def plan_node(state: AgentState) -> dict:
        user_message = state["messages"][-1] if state["messages"] else ""
        workspace_id = state["workspace_id"]
        plan_auto_approve = True
        if settings_service is not None:
            try:
                plan_auto_approve = bool(settings_service.get("agent:plan_auto_approve"))
            except KeyError:
                plan_auto_approve = True

        console.step("规划节点", "分析节点", user_message)

        workspace_info = workspace_service.get_workspace_info(workspace_id)
        session_id = workspace_info.get("session_id", "default") if workspace_info else "default"

        if llm_service:
            system_prompt = """你是一个软件工程任务规划器。

请只输出高层计划纲要，严格使用 JSON：
{
  "tasks": [
    {
      "description": "步骤描述",
      "goal": "该步骤要达成的目标",
      "done_when": "满足什么条件说明该步骤完成",
      "phase": "research|synthesis|implementation|verification"
    }
  ]
}

要求：
1. 只输出 2-5 个高层步骤
2. 不要在这里生成 tool 或具体 args
3. description 要描述做什么，goal 要描述为什么做，done_when 要描述完成判定
4. 输出必须是 JSON
"""

            messages = [{"role": "user", "content": f"请为以下任务生成高层执行计划：\n\n{user_message}"}]

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
        if plan_auto_approve:
            plan_content = plan_content.replace("*此计划由 Agent 自动生成，请审核后批准执行。*", "*此计划由 Agent 自动生成，已根据配置自动继续执行。*")

        create_result = plan_file_service.create_plan(
            session_id=session_id,
            workspace_id=workspace_id,
            plan_content=plan_content,
            plan_steps=plan,
            metadata={"task_description": user_message}
        )

        console.box("计划文件已创建", create_result.get("plan_file"))

        if message_context:
            send_message = message_context.get("send_message")
            if send_message:
                state_metadata = {
                    "execution_mode": "PLAN",
                    "plan_steps": len(plan),
                    "plan_file": create_result.get("plan_file"),
                    "plan_auto_approve": plan_auto_approve,
                }
                send_message("", SegmentType.STATE_CHANGE, state_metadata)
                send_message(plan_content, SegmentType.TEXT_DELTA)

        if plan_auto_approve:
            todos = _hydrate_todos_from_plan(plan_content, workspace_id)
            console.decision_box("decide", "计划已生成，已转为 todo，开始 DIRECT 执行")
            first_todo = todos[0] if todos else {}
            return {
                "plan": plan,
                "plan_file": create_result.get("plan_file"),
                "todos": todos,
                "current_todo_index": 0,
                "current_todo_goal": first_todo.get("goal"),
                "current_todo_done_when": first_todo.get("done_when"),
                "current_todo_iteration_count": 0,
                "todo_status": "pending",
                "execution_mode": ExecutionMode.DIRECT,
                "mode_reason": "计划已生成并转为 todo，进入统一执行循环",
                "results": [],
                "has_tool_use": False,
                "final_reply": None,
                "pending_tools": [],
            }

        console.decision_box("done", "计划已生成，等待用户确认执行")
        return {
            "plan": plan,
            "plan_file": create_result.get("plan_file"),
            "final_reply": plan_content
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
        from ...service import WorkspaceService
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
) -> dict:
    if not llm_service:
        result = f"回复任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    console.info("调用 LLM 进行对话回复...")
    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", SegmentType.CHAT_START, {
            "task_description": task_description,
            "is_start": True
        })

    try:
        full_prompt = build_context_prompt(
            parent_chain_messages,
            current_conversation_messages,
            f"请向用户输出回复: {task_description}"
        )
        messages = [{"role": "user", "content": full_prompt}]

        def chat_token_callback(token: str):
            if send_message:
                send_message(token, SegmentType.CHAT_DELTA, {
                    "task_description": task_description,
                    "is_delta": True
                })

        result = ""
        for chunk in llm_service.chat_stream(messages, CHAT_SYSTEM_PROMPT, chat_token_callback):
            result += chunk

        console.success("对话回复完成")

        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": task_description,
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": task_description,
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
            task_description = tool_args.get("description", "")

            console.step("执行节点", "分析节点", f"执行工具: {tool_name}")

            console.box("执行工具", {
                "工具名称": tool_name,
                "工具参数": tool_args
            })

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
                message_context=message_context,
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
            new_current_conv_msgs.append({
                "role": "assistant",
                "content": f"[工具执行: {tool_name}]\n结果: {result_str[:1000]}"
            })

            tool_success = tool_result.get("error") is None
            tool_error = tool_result.get("error")

            if execution_mode == ExecutionMode.DIRECT and tool_name != "chat":
                return {
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

            if execution_mode == ExecutionMode.PLAN and tool_name != "chat":
                return {
                    "pending_tools": [],
                    "tool_history": new_tool_history,
                    "current_conversation_messages": new_current_conv_msgs,
                    "has_tool_use": False,
                    "last_tool_result": result_str,
                    "last_tool_name": tool_name,
                    "last_tool_success": tool_success,
                    "last_tool_error": tool_error,
                    "current_step_iteration_count": (state.get("current_step_iteration_count", 0) or 0) + 1,
                }

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


def create_replan_remaining_steps_node(llm_service=None, settings_service=None, message_context=None):
    def replan_remaining_steps_node(state: AgentState) -> dict:
        plan = state.get("plan") or []
        todos = state.get("todos") or []
        current_todo_index = state.get("current_todo_index", 0) or 0
        reason = state.get("replan_reason") or "todo 执行受阻"
        user_message = state["messages"][-1] if state["messages"] else ""
        workspace_id = state["workspace_id"]

        completed_todos = todos[:current_todo_index]
        remaining_todos = todos[current_todo_index:]
        completed_summary = "\n".join(
            f"- {item.get('description')} | status={item.get('status')} | result={item.get('result')}"
            for item in completed_todos
        ) or "(无已完成 todo)"
        remaining_summary = "\n".join(
            f"- {item.get('description')} | goal={item.get('goal')} | done_when={item.get('done_when')}"
            for item in remaining_todos
        ) or "(无剩余 todo)"

        if llm_service is None:
            reply = f"当前计划执行受阻：{reason}"
            _emit_final_reply(reply, message_context)
            return {
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "execution_mode": None,
            }

        system_prompt = """你是一个计划软件重规划器。

请只重写尚未完成的剩余任务，严格输出 JSON：
{
  "tasks": [
    {
      "description": "任务描述",
      "goal": "该任务要达成的目标",
      "done_when": "满足什么条件说明该任务完成",
      "phase": "research|synthesis|implementation|verification"
    }
  ]
}

要求：
1. 保留已完成任务，不要重写它们
2. 只重排当前及之后的剩余任务
3. 输出 1-5 个剩余任务
4. 不要生成 tool 或 args
5. 输出必须是 JSON
"""

        prompt = (
            f"原始用户请求: {user_message}\n\n"
            f"重规划原因: {reason}\n\n"
            f"已完成 todo:\n{completed_summary}\n\n"
            f"待重排 todo:\n{remaining_summary}\n\n"
            "请只重写剩余任务。"
        )

        try:
            response = llm_service.chat([{"role": "user", "content": prompt}], system_prompt=system_prompt)
            response_text = response.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            import json
            data = json.loads(response_text)
            raw_tasks = data.get("tasks") if isinstance(data, dict) else None
            if not raw_tasks:
                raise ValueError("重规划结果缺少 tasks")

            rebuilt_remaining = []
            for offset, task in enumerate(raw_tasks, 1):
                rebuilt_remaining.append({
                    "id": current_todo_index + offset,
                    "description": task.get("description") or f"重规划任务 {offset}",
                    "goal": task.get("goal") or task.get("description") or f"完成重规划任务 {offset}",
                    "done_when": task.get("done_when") or "该任务目标达成",
                    "phase": task.get("phase") or "implementation",
                    "status": "pending",
                    "tool": None,
                    "args": None,
                    "result": None,
                    "feedback": None,
                })

            updated_plan = plan[:current_todo_index] + rebuilt_remaining
            updated_plan_content = plan_file_service.format_plan_as_markdown(user_message, updated_plan)
            workspace_info = workspace_service.get_workspace_info(workspace_id)
            session_id = workspace_info.get("session_id", "default") if workspace_info else "default"
            plan_file_service.update_plan(
                session_id=session_id,
                workspace_id=workspace_id,
                plan_content=updated_plan_content,
                plan_steps=updated_plan,
            )

            hydrated_todos = _hydrate_todos_from_plan(updated_plan_content, workspace_id)
            next_todo = hydrated_todos[current_todo_index] if current_todo_index < len(hydrated_todos) else {}
            return {
                "plan": updated_plan,
                "todos": hydrated_todos,
                "current_todo_index": current_todo_index,
                "current_todo_goal": next_todo.get("goal"),
                "current_todo_done_when": next_todo.get("done_when"),
                "current_todo_iteration_count": 0,
                "todo_status": "pending",
                "replan_reason": None,
                "pending_tools": [],
                "has_tool_use": False,
            }
        except Exception as e:
            reply = f"当前计划执行受阻，且重规划失败：{e}"
            _emit_final_reply(reply, message_context)
            return {
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "execution_mode": None,
            }

    return replan_remaining_steps_node


def route_after_step_review(state: AgentState) -> str:
    if state.get("replan_reason"):
        return "replan"

    return state.get("step_status") or "plan_step_decide"


def create_advance_plan_step_node():
    def advance_plan_step_node(state: AgentState) -> dict:
        plan = state.get("plan") or []
        current_step = state.get("current_step", 0)
        if current_step >= len(plan):
            return {"final_reply": "任务已完成。", "has_tool_use": False}

        plan[current_step]["status"] = "completed"
        plan[current_step]["result"] = state.get("last_tool_result")
        next_index = current_step + 1
        if next_index >= len(plan):
            return {
                "plan": plan,
                "current_step": next_index,
                "step_status": "all_done",
                "has_tool_use": False,
                "pending_tools": [],
            }

        next_step = plan[next_index]
        return {
            "plan": plan,
            "current_step": next_index,
            "current_step_goal": next_step.get("goal"),
            "current_step_done_when": next_step.get("done_when"),
            "current_step_iteration_count": 0,
            "step_status": "pending",
            "pending_tools": [],
            "has_tool_use": False,
        }

    return advance_plan_step_node


def route_after_todo_review(state: AgentState) -> str:
    if state.get("replan_reason"):
        return "replan"
    return "decide"


def route_after_execute(state: AgentState) -> str:
    if state.get("execution_mode") == ExecutionMode.DIRECT and not state.get("pending_tools"):
        return "todo_review"
    return check_state_v3(state)


def create_orchestrator_graph_v3(llm_service=None, token_callback=None, memory_mode: str = "accumulate", window_size: int = 3, settings_service=None, message_context=None):
    graph = StateGraph(AgentState)
    plan_auto_approve = True
    if settings_service is not None:
        try:
            plan_auto_approve = bool(settings_service.get("agent:plan_auto_approve"))
        except KeyError:
            plan_auto_approve = True

    graph.add_node("analyze", create_analyze_node(llm_service, message_context, settings_service))
    graph.add_node("decide", create_decide_next_action_node(llm_service, settings_service, message_context))
    graph.add_node("plan", create_plan_node(llm_service, token_callback, settings_service, message_context))
    graph.add_node("todo_review", create_step_review_node(llm_service, message_context))
    graph.add_node("replan", create_replan_remaining_steps_node(llm_service, settings_service, message_context))
    graph.add_node("execute", create_execute_node(llm_service, token_callback, settings_service, message_context))

    graph.set_entry_point("analyze")

    graph.add_conditional_edges("analyze", route_after_analyze, {
        "plan": "plan",
        "decide": "decide",
        "done": END
    })

    graph.add_conditional_edges("decide", check_state_v3, {
        "analyze": "analyze",
        "decide": "decide",
        "execute": "execute",
        "plan": "plan",
        "done": END
    })

    graph.add_conditional_edges("plan", check_state_v3, {
        "analyze": "analyze",
        "decide": "decide",
        "execute": "execute",
        "plan": "plan",
        "done": END
    })

    graph.add_conditional_edges("execute", route_after_execute, {
        "analyze": "analyze",
        "decide": "decide",
        "todo_review": "todo_review",
        "execute": "execute",
        "plan": "plan",
        "done": END
    })

    graph.add_conditional_edges("todo_review", route_after_todo_review, {
        "decide": "decide",
        "replan": "replan",
    })

    graph.add_edge("replan", "decide")

    return graph.compile()


def run_graph_v3(
    user_message: str,
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

    if final_state.get("is_root_graph") and message_context:
        send_message = message_context.get("send_message")
        if send_message:
            send_message("", SegmentType.DONE, {
                "message_id": message_context.get("message_id", "")
            })
    
    print("\n" + "="*60)
    print("[Director Agent] 主编排图执行完成")
    print("="*60)
    
    return final_state


run_graph_v2 = run_graph_v3
create_orchestrator_graph_v2 = create_orchestrator_graph_v3
check_state_v2 = check_state_v3
