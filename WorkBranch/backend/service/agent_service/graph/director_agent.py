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
from .definitions import get_definition
from .agent_definition import AgentDefinition, calculate_recursion_limit
from ..state import AgentState
from .subgraphs.tool_registry import (
    is_tool_allowed, get_allowed_tools, _write_tool_event
)
from .subgraphs.tool_executor import run_tool_execution, _get_subagent_timeout
from .react_agent_base import ReActAgentBase, MemoryManager
from .definitions import get_definition
from service.agent_service.prompts.graph_prompts import (
    THINK_SYSTEM_PROMPT,
    PLAN_MODE_SYSTEM_PROMPT,
    DIRECT_SYSTEM_PROMPT,
    build_chat_system_prompt as _graph_build_chat_system_prompt,
    build_context_prompt as _graph_build_context_prompt,
    build_direct_chat_messages as _graph_build_direct_chat_messages,
    build_director_plan_messages,
    build_tool_schema_prompt as _graph_build_tool_schema_prompt,
    format_todo_prompt_block as _graph_format_todo_prompt_block,
    generate_prompt,
)
from service.agent_service.prompts.error_injection import (
    ToolCallError,
    create_json_format_error,
    create_tool_name_error,
)
from service.session_service.canonical import SegmentType
from service.agent_service.service.plan_file_service import plan_file_service
from service.agent_service.service.workspace_service import WorkspaceService
from service.session_service.message_content import build_prompt_safe_text, get_message_parts, get_message_text, has_image_parts
from service.agent_service.tools.todo_tools import build_todo_agent_state_update, restore_todo_checkpoint
from core.logging import console, open_trace_log
from singleton import get_workspace_service

MAX_REPLAN_COUNT = 3
MAX_MESSAGES = 10
# max_iterations 已迁移到 AgentDefinition.meta.max_iterations
# 各 Agent 在 director_def.py, explore_def.py 等文件中配置
CHECK_INTERVAL = 8

workspace_service = get_workspace_service()


def _levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串之间的编辑距离"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


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
        args_str = str(args) if args else "{}"
        result_preview = str(item.get("result", ""))
        history_lines.append(
            f"第{idx}轮: 工具={tool_name}, 参数={args_str}, 结果摘要={result_preview}..."
        )
    history_block = "\n".join(history_lines) if history_lines else "(暂无工具调用历史)"
    
    user_message_block = ""
    if user_message:
        user_message_block = f"""
## 用户原始请求
{user_message}
"""
    
    conversation_block = ""
    if conversation_history:
        conv_lines = []
        for msg in conversation_history[-6:]:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))
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
            content = str(todo.get("content", ""))
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

    # 规则预检：最近窗口内同一工具+相同参数+相同结果重复出现 >=3 次，判定为循环。
    # 排除纯查询/思考类工具，避免误伤正常的多文件流程。
    from collections import Counter

    recent = tool_history[-CHECK_INTERVAL:] if len(tool_history) >= CHECK_INTERVAL else tool_history
    if len(recent) >= 6:
        query_tools = {"list_workspace_files", "get_workspace_info", "search_files", "thinking"}
        sigs = []
        for item in recent:
            tname = item.get("tool_name") or item.get("tool") or "unknown"
            if tname in query_tools:
                continue
            sigs.append((str(tname), str(item.get("args")), str(item.get("result") or item.get("error") or "")))
        counter = Counter(sigs)
        for (tname, _, _), count in counter.items():
            if count >= 3:
                return {
                    "action": "stop",
                    "reason": f"检测到重复调用相同工具且参数与结果完全相同（{tname} × {count}），判定为循环",
                }
    
    prompt = _build_loop_check_prompt(
        tool_history, 
        iteration_count,
        user_message=user_message,
        conversation_history=conversation_history,
        todos=todos,
    )
    
    try:
        if isinstance(llm_service, LLMService):
            response = llm_service.chat_with_json_mode(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
        else:
            # 兜底：非 LLMService 实例走 chat 模式（保持原行为，依赖下游剥 ```json 包裹）
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
        "pending_tools": [{"tool_name": "chat", "args": tool_args}],
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
    definition: AgentDefinition = None,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None,
    agent_type: Optional[str] = None,
    is_root_graph: bool = False,
    forced_execution_mode: Optional[ExecutionMode] = None,
    plan_file: Optional[str] = None,
    plan_content: Optional[str] = None,
    prior_agent_state: Optional[AgentState] = None,
) -> dict:
    # 从 definition 读取 max_iterations，默认为 10
    max_iterations = definition.meta.max_iterations if definition else 10
    todo_state = restore_todo_checkpoint(prior_agent_state, workspace_id)

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
        "execution_mode": ExecutionMode.DIRECT,
        "last_tool_result": None,
        "iteration_count": 0,
        "max_iterations": max_iterations,
        "next_action": None,
        "last_tool_name": None,
        "last_tool_success": None,
        "last_tool_error": None,
        "invalid_tool_retry_count": 0,
        "todos": list(todo_state.todos),
        "current_todo_index": todo_state.doingIdx,
        "current_todo_goal": None,
        "current_todo_done_when": None,
        "current_todo_iteration_count": 0,
        "todo_max_iterations": max_iterations,
        "todo_status": "pending" if todo_state.todos else None,
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


def check_state_node(state: AgentState) -> dict:
    """状态检查节点（原 check_state_v3，从 edge 函数改为节点函数）

    作为节点执行时能读到完整的 merged state（包括前一个节点的最新更新），
    解决了 edge 函数中 LangGraph 状态传播延迟导致 pending_tools 读取旧值的问题。

    返回 dict 中包含 _route_target 字段，由 _route_after_check_state edge 函数读取。
    """
    # 添加详细日志追踪状态
    _iter = state.get("iteration_count", 0) or 0
    _pending = len(state.get("pending_tools") or [])
    _final = state.get("final_reply")
    _forced_err = state.get("force_error_summary")
    _dec_err = state.get("decision_error_count", 0) or 0
    print(f"[TRACE check_state] iter={_iter}, pending={_pending}, final_reply={bool(_final)}, force_err={_forced_err}, dec_err={_dec_err}")

    iteration_count = state.get("iteration_count", 0) or 0
    max_iterations = state.get("max_iterations", 10) or 10

    # ===== 前置迭代次数检查 =====
    if iteration_count >= max_iterations:
        console.warning(f"[check_state] 迭代次数已达上限 ({iteration_count}/{max_iterations})，强制终止")
        return {"_route_target": "error_summary"}

    # ===== 检查 pending_tools（此时已是 decide 节点写入后的最新值）=====
    if state.get("pending_tools"):
        print(f"[TRACE check_state] -> execute (pending_tools exists, count={_pending})")
        return {"_route_target": "execute"}

    if state.get("final_reply"):
        return {"_route_target": "done"}

    if state.get("force_error_summary"):
        return {"_route_target": "error_summary"}

    # ===== 决策错误保护 =====
    decision_error_count = state.get("decision_error_count", 0) or 0
    if decision_error_count >= 3:
        console.warning(f"[check_state] 决策连续失败 {decision_error_count} 次，强制终止")
        return {"_route_target": "error_summary"}

    # ===== 检查 todo_status = step_done =====
    todo_status = state.get("todo_status")
    if todo_status == "step_done":
        console.info("[check_state] 检测到 todo_status=step_done，任务完成")
        return {"_route_target": "done"}

    # ===== 检测工具失败循环 =====
    # 注意：chat 兜底逻辑已移除。step_done 时的 chat 兜底由 decide 节点处理，
    # check_state_node 不再无条件注入 chat，避免 update_todo 后误触发。
    tool_history = state.get("tool_history", []) or []
    last_tool_success = state.get("last_tool_success")
    last_tool_name = state.get("last_tool_name")

    try:
        from singleton import get_settings_service
        _hard_limit = int(get_settings_service().get("agent:iterations:director:hard_limit"))
    except (KeyError, ValueError, ImportError):
        _hard_limit = 256
    if iteration_count > _hard_limit:
        console.warning(f"[check_state] 迭代次数过多 ({iteration_count}/{_hard_limit})，强制终止")
        return {"_route_target": "error_summary"}

    if last_tool_success is False and last_tool_name:
        recent_failures = 0
        repeated_same_tool = 0
        last_failed_tool = None
        last_failed_args = None

        for item in reversed(tool_history[-10:]):
            if item.get("tool_name") == last_tool_name:
                if item.get("result") is None or item.get("error"):
                    recent_failures += 1
                    if last_failed_tool == last_tool_name:
                        current_args = item.get("args", {})
                        if last_failed_args == current_args:
                            repeated_same_tool += 1
                        last_failed_args = current_args
                    last_failed_tool = last_tool_name

        if repeated_same_tool >= 3:
            console.warning(
                f"[check_state] 工具失败循环: {last_tool_name} 连续失败 {repeated_same_tool} 次"
            )
            return {"_route_target": "error_summary"}

        if recent_failures >= 4 and len(tool_history) >= 5:
            console.warning(
                f"[check_state] 工具失败循环: 最近5次中有 {recent_failures} 次失败"
            )
            return {"_route_target": "error_summary"}

    # 所有检查通过，回到 decide 让 LLM 继续决策
    print(f"[TRACE check_state] -> decide (tool_history_len={len(tool_history)})")
    return {"_route_target": "decide"}


def _route_after_check_state(state: AgentState) -> str:
    """check_state 节点的路由函数 — 只读取节点写入的 _route_target"""
    target = state.get("_route_target") or "decide"
    return target


# 保留旧函数名作为别名供外部引用（兼容）
check_state_v3 = check_state_node


def route_after_analyze(state: dict) -> str:
    """路由 after_analyze — 理论上 analyze 不应该返回 pending_tools"""
    pending = state.get("pending_tools")
    if pending:
        console.warning(f"[route_after_analyze] ⚠️ 意外：analyze 返回了 pending_tools (count={len(pending)})")
        return "execute"
    # 直接进入决策，不需要绕路
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
            "summary": user_message,
            "key_points": [user_message] if user_message else [],
            "complexity": "medium",
            "confidence": 0.7,
        }

        # analyze 始终只做模式分析，不直接注入工具
        # pending_tools 始终为空，由 decide 阶段决策下一步
        result = {
            "intent_analysis": intent_analysis,
            "execution_mode": mode_decision["mode"],
            "mode_reason": mode_decision["reason"],
            "suggested_tools": [],
            "has_tool_use": False,
            "final_reply": None,
            "pending_tools": [],  # analyze 不返回工具，统一在 decide 决策
            "next_action": None,
        }

        # 多模态支持：检测到图片输入时，直接在 decide 阶段注入 chat 工具
        if _should_use_native_multimodal_chat(state, _settings_service):
            # 多模态图片输入，不需要 LLM 决策，直接生成 chat 回复
            result["pending_tools"] = [{
                "tool_name": "chat",
                "args": {
                    "description": user_message or "请直接分析这张图片并回答用户。",
                    "multimodal_parts": state.get("current_user_message_parts") or get_last_user_message_parts(state),
                }
            }]
            result["has_tool_use"] = True
            result["mode_reason"] = "检测到图片输入，DIRECT 模式直接走原生多模态 chat"
            console.decision_box("execute", f"执行模式: {result['execution_mode']} (多模态图片输入，跳过 LLM 决策)")
            return result

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


def _format_todo_prompt_block(todos: List[str], current_todo_index: int) -> str:
    return _graph_format_todo_prompt_block(todos, current_todo_index)


def _handle_decision_error(exception: Exception, response_text: str, state: AgentState,
                          user_message: str, message_context: dict, iteration_count: int) -> dict:
    """
    统一处理决策异常：输出完整错误信息，支持3次重试，超过3次强制终止

    Args:
        exception: 捕获的异常对象
        response_text: LLM原始响应文本（可能为空）
        state: 当前graph状态
        user_message: 用户原始消息
        message_context: 消息上下文
        iteration_count: 当前迭代次数

    Returns:
        graph状态更新字典（重试或终止）
    """
    # 记录到文件日志（保留原有逻辑）
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    try:
        with open_trace_log() as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] === ❌ DIRECTOR AGENT DECISION EXCEPTION ===\n")
            f.write(f"[{timestamp}] Exception Type: {type(exception).__name__}\n")
            f.write(f"[{timestamp}] Exception Message: {str(exception)}\n")
            f.write(f"[{timestamp}] Response text ({len(response_text)} chars):\n{response_text}\n")
            f.write(f"{'='*80}\n")
            f.flush()
    except Exception:
        pass

    # 计算错误次数
    decision_error_count = (state.get("decision_error_count", 0) or 0) + 1

    console.warning(
        f"[DECISION-RETRY] 决策失败 #{decision_error_count}/3 - "
        f"{type(exception).__name__}: {str(exception)}"
    )

    if decision_error_count >= 3:
        # ✅ 超过3次强制终止，必须有明确提示（完整输出，禁止截断）
        error_msg = (
            f"LLM决策连续失败3次，最后一次错误: [{type(exception).__name__}] {str(exception)}\n"
            f"原始响应: {response_text if response_text else '(空)'}"
        )
        console.warning(f"[DECISION-FATAL] {error_msg}")
        _emit_final_reply(error_msg, message_context)
        return {
            "next_action": {"kind": "reply", "reply": error_msg, "task_description": user_message},
            "final_reply": error_msg,
            "has_tool_use": False,
            "pending_tools": [],
            "force_error_summary": True,
            "error_summary_type": "decision_repeated_failure",
            "decision_error_count": decision_error_count,
        }

    # ✅ 重试时必须注入详细错误信息到下一次prompt
    console.warning(
        f"[DECISION-RETRY] 将使用相同提示词重试 (第{decision_error_count}/3次)，"
        f"已将错误信息注入prompt上下文"
    )

    last_error = create_json_format_error(
        original_json=response_text,
        parse_error=f"[{type(exception).__name__}] {str(exception)}"
    )
    return {
        "next_action": None,
        "final_reply": None,
        "has_tool_use": False,
        "pending_tools": [],
        "decision_error_count": decision_error_count,
        "last_error": last_error,
        "iteration_count": iteration_count,
    }


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
        iteration_count = (state.get("iteration_count", 0) or 0) + 1
        max_iterations = state.get("max_iterations", 10) or 10
        todos = state.get("todos") or []

        console.step(title, subtitle, f"第 {iteration_count}/{max_iterations} 轮")

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
        tool_schema_prompt = _graph_build_tool_schema_prompt(allowed_tools, agent_type=current_agent_type)

        plan_content = None
        if not is_plan_mode:
            plan_content, _ = _load_plan_content_for_state(state)

        system_prompt, context_prompt = generate_prompt(
            agent_type=current_agent_type,
            mode="PLAN" if is_plan_mode else "DIRECT",
            user_message=user_message,
            workspace_id=state['workspace_id'],
            iteration_count=iteration_count,
            max_iterations=max_iterations,
            tool_schema_prompt=tool_schema_prompt,
            tool_history=tool_history,
            last_tool_result=last_tool_result,
            todos=todos,
            current_todo_index=state.get("current_todo_index", 0) or 0,
            plan_content=plan_content,
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            last_error=state.get("last_error"),
        )

        # 在 LLM 调用前记录完整的请求上下文（确保异常时也有记录）
        try:
            import datetime
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            with open_trace_log() as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"[{timestamp}] === 🔄 DIRECTOR LLM REQUEST START ===\n")
                f.write(f"[{timestamp}] Agent Type: {current_agent_type}\n")
                f.write(f"[{timestamp}] Mode: {'PLAN' if is_plan_mode else 'DIRECT'}\n")
                f.write(f"[{timestamp}] Iteration: {iteration_count}/{max_iterations}\n")
                f.write(f"[{timestamp}] Tool history: {len(tool_history)} items\n")
                if tool_history:
                    for idx, item in enumerate(tool_history[-5:], 1):
                        f.write(f"[{timestamp}]   history[{idx}]: tool={item.get('tool_name', 'N/A')}, result_len={len(str(item.get('result', '')))}\n")
                f.write(f"[{timestamp}] Last tool result: {str(last_tool_result) if last_tool_result else 'None'}\n")
                f.write(f"\n[{'='*40} SYSTEM PROMPT ({len(system_prompt)} chars) {'='*40}\n")
                f.write(system_prompt if system_prompt else "(empty)")
                f.write(f"\n[{'='*40} USER MESSAGE ({len(context_prompt)} chars) {'='*40}\n")
                f.write(context_prompt if context_prompt else "(empty)")
                f.write(f"\n[{timestamp}] === 🔄 DIRECTOR LLM REQUEST END ===\n\n")
                f.flush()
        except Exception as log_err:
            pass

        response = None
        try:
            # 使用厂商 JSON Mode 强制返回纯 JSON，避免手工剥 ```json 包裹
            response = llm_service.chat_with_json_mode(
                messages=[{"role": "user", "content": context_prompt}],
                system_prompt=system_prompt,
            )
            console.response_box(response)
            response_text = response.strip()

            # 记录 LLM 响应
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            with open_trace_log() as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"[{timestamp}] === 🤖 DIRECTOR LLM RAW RESPONSE ===\n")
                f.write(f"[{timestamp}] Raw response ({len(response_text)} chars):\n{response_text}\n")
                f.write(f"{'='*80}\n")
                f.flush()

            # 防御性处理：检查 LLM 是否返回空响应
            if not response_text:
                raise ValueError("LLM 返回了空响应，可能是 API 超时或模型异常")

            decision_data = json.loads(response_text)
            # 防御：LLM 偶尔返回 JSON 数组 [{...}] 而非对象 {...}，取第一个元素
            if isinstance(decision_data, list):
                console.warning(f"[LLM-PARSE-WARN] LLM 返回了数组而非对象，取第一个元素")
                if len(decision_data) == 0:
                    raise ValueError("LLM 返回了空数组")
                decision_data = decision_data[0]
            # ✅ 分类异常：JSON解析错误
            console.warning(f"[LLM-PARSE-ERROR] JSON解析失败 (第{decision_error_count+1}次): {e}")
            console.warning(f"[LLM-PARSE-ERROR] 原始响应内容:\n{response_text}")
            _handle_decision_error(e, response_text, state, user_message, message_context, iteration_count)

        except httpx.TimeoutException as e:
            # ✅ 分类异常：网络超时
            console.warning(f"[LLM-NETWORK-ERROR] API调用超时 (第{decision_error_count+1}次): {e}")
            _handle_decision_error(e, "", state, user_message, message_context, iteration_count)

        except ValueError as e:
            # ✅ 分类异常：空响应/模型异常
            console.warning(f"[LLM-VALUE-ERROR] 响应验证失败 (第{decision_error_count+1}次): {e}")
            _handle_decision_error(e, response_text if 'response_text' in dir() else "", state, user_message, message_context, iteration_count)

        except Exception as e:
            # ✅ 其他未知异常
            import traceback
            console.warning(f"[LLM-UNKNOWN-ERROR] {type(e).__name__} (第{decision_error_count+1}次): {str(e)}")
            console.warning(f"[LLM-UNKNOWN-ERROR] 完整堆栈:\n{traceback.format_exc()}")
            _handle_decision_error(e, response_text if 'response_text' in dir() else "", state, user_message, message_context, iteration_count)

        kind = decision_data.get("kind")
        if kind == "step_done":
            # ===== 【关键修复】检查 Agent 是否遗漏了 chat 工具调用 =====
            # 当返回 step_done 时，如果最后一个工具调用不是 chat，应该自动跑一次 chat 工具汇总结果
            has_chat_call = any(t.get("tool_name") == "chat" for t in tool_history)
            last_tool = tool_history[-1] if tool_history else None
            last_tool_name = last_tool.get("tool_name") if last_tool else None

            if not has_chat_call:
                # Agent 完成了数据分析但忘记调用 chat 工具输出结果
                # 强制注入 chat 工具调用
                console.warning(f"[create_decide_tool_action_node] Agent 遗漏了 chat 工具调用（最后工具: {last_tool_name}），强制注入最终回复输出")

                # 🔧 修复：将之前的工具执行结果注入到 chat 工具参数中
                previous_results = [
                    {
                        "tool_name": item.get("tool_name", "unknown"),
                        "result": item.get("result", ""),
                        "reason": "",
                        "timestamp": item.get("timestamp", ""),
                    }
                    for item in tool_history
                    if item.get("result")
                ]

                return {
                    "pending_tools": [{"tool_name": "chat", "args": {
                        "description": f"请汇总已提取的桥梁病害信息，以结构化格式输出最终结果。",
                        "previous_results": previous_results,  # 🔧 关键修复：传递历史结果
                    }}],
                    "has_tool_use": True,
                    "todo_status": None,  # 清除 step_done 状态，等待 chat 执行
                    "last_error": None,  # 清除错误信息
                    "iteration_count": iteration_count,
                }

            # 正常情况：已经有 chat 调用，可以结束
            return {
                "todo_status": "step_done",
                "has_tool_use": False,
                "pending_tools": [],
                "last_error": None,  # 清除错误信息
                "iteration_count": iteration_count,
            }
        if kind == "blocked":
            reply = decision_data.get("reply") or "当前 todo 被阻塞"
            _emit_final_reply(reply, message_context)
            return {
                "todo_status": "blocked",
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "last_error": None,  # 清除错误信息
                "iteration_count": iteration_count,
            }

        tool_name = decision_data.get("tool_name") or decision_data.get("name")
        tool_args = decision_data.get("tool_args") or decision_data.get("args") or {}
        task_description = decision_data.get("task_description") or user_message

        if not tool_name or not is_tool_allowed(tool_name, current_agent_type, settings_service):
            console.box("无效工具决策原始回复", json.dumps(decision_data, ensure_ascii=False, indent=2))
            retry_count = (state.get("invalid_tool_retry_count", 0) or 0) + 1
            if retry_count <= 3:
                console.decision_box("decide", f"工具决策无效，使用相同提示词重试第 {retry_count}/3 次")
                # 设置错误信息，让下一次 prompt 注入错误提示
                allowed_tools = get_allowed_tools(current_agent_type, settings_service)
                suggestions = [t for t in allowed_tools if tool_name and (t.startswith(tool_name[:3]) or _levenshtein_distance(tool_name, t) <= 3)]
                last_error = create_tool_name_error(
                    invalid_name=tool_name or "",
                    suggestions=suggestions[:3],  # 最多提供3个建议
                    original_json=json.dumps(decision_data, ensure_ascii=False)
                )
                return {
                    "pending_tools": [],
                    "has_tool_use": False,
                    "final_reply": None,
                    "next_action": None,
                    "invalid_tool_retry_count": retry_count,
                    "last_error": last_error,
                    "iteration_count": iteration_count,
                }

            reply = f"工具决策无效，无法继续执行：{tool_name}；原始回复：{json.dumps(decision_data, ensure_ascii=False)}"
            _emit_final_reply(reply, message_context)
            return {
                "next_action": {"kind": "reply", "reply": reply, "task_description": task_description},
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "invalid_tool_retry_count": retry_count,
                "iteration_count": iteration_count,
            }

        pending = [{"tool_name": tool_name, "args": dict(tool_args)}]
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
            "last_error": None,  # 清除错误信息
            "iteration_count": iteration_count,
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
                # 使用厂商 JSON Mode 强制返回纯 JSON，避免手工剥 ```json 包裹
                response = llm_service.chat_with_json_mode(messages, system_prompt=system_prompt)

                console.response_box(response)

                import json
                response_text = response.strip()

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
            "pending_tools": [{"tool_name": "chat", "args": {"description": chat_description}}],
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
                                    "content": line.strip()
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
                result_lines.append(f"   摘要: {body}")
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

    subagent_timeout = _get_subagent_timeout(settings_service)

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
                outcome = future.result(timeout=subagent_timeout)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": f"explore_agent 子图执行超时（{subagent_timeout}秒）",
                        "details": {"agent_type": "explore_agent", "timeout_seconds": subagent_timeout},
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

    subagent_timeout = _get_subagent_timeout(settings_service)

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
                outcome = future.result(timeout=subagent_timeout)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": f"review_agent 子图执行超时（{subagent_timeout}秒）",
                        "details": {"agent_type": "review_agent", "timeout_seconds": subagent_timeout},
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


# ============================================================================
# Director Agent 特殊工具策略（用于覆盖 ReActAgentBase 默认策略）
# ============================================================================

def _director_thinking_strategy(
    tool_name: str,
    tool_args: dict,
    task_description: str,
    llm_service,
    message_context: dict,
    config: dict,
) -> dict:
    """Director Agent 的 thinking 工具策略
    
    与子Agent的差异：
    - 使用 THINK_SYSTEM_PROMPT（而非通用提示词）
    - 使用 build_context_prompt 构建上下文
    """
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
        parent_chain_messages = message_context.get("parent_chain_messages", []) if message_context else []
        current_conversation_messages = message_context.get("current_conversation_messages", []) if message_context else []
        
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


def _director_chat_strategy(
    tool_name: str,
    tool_args: dict,
    task_description: str,
    llm_service,
    message_context: dict,
    config: dict,
) -> dict:
    """Director Agent 的 chat 工具策略

    🔧 关键修复：
    - 直接使用当前 agent 的完整提示词（与 decide 节点相同）
    - 在底部注入：当前任务是向用户回复（主题：...）
    """
    if not llm_service:
        result = f"回复任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    # 从 config(state) 获取完整状态
    state = config if isinstance(config, dict) else {}
    current_agent_type = state.get("agent_type") or "director_agent"
    execution_mode = state.get("execution_mode")
    is_plan_mode = _mode_name(execution_mode) == "PLAN"

    # 获取主题（从 tool_args 或 task_description）
    chat_topic = (
        tool_args.get("description")
        or tool_args.get("next_task")
        or tool_args.get("topic")
        or task_description
        or "向用户输出回复"
    )

    # 获取与 decide 节点相同的所有上下文
    user_message = get_last_user_message_text(state)
    tool_history = state.get("tool_history", []) or []
    last_tool_result = state.get("last_tool_result")
    iteration_count = (state.get("iteration_count", 0) or 0) + 1
    max_iterations = state.get("max_iterations", 10) or 10
    todos = state.get("todos") or []

    parent_chain_messages = message_context.get("parent_chain_messages", []) if message_context else []
    current_conversation_messages = message_context.get("current_conversation_messages", []) if message_context else []

    # 加载工具列表和 schema
    settings_service = message_context.get("settings_service") if message_context else None
    allowed_tools = get_allowed_tools(current_agent_type, settings_service)
    tool_schema_prompt = _graph_build_tool_schema_prompt(allowed_tools, agent_type=current_agent_type)

    # 加载 plan 内容
    plan_content = None
    if not is_plan_mode:
        plan_content, _ = _load_plan_content_for_state(state)

    # 使用与 decide 节点相同的 generate_prompt 构建完整提示词
    from service.agent_service.prompts.graph_prompts import generate_prompt
    system_prompt, context_prompt = generate_prompt(
        agent_type=current_agent_type,
        mode="PLAN" if is_plan_mode else "DIRECT",
        user_message=user_message,
        workspace_id=state['workspace_id'],
        iteration_count=iteration_count,
        max_iterations=max_iterations,
        tool_schema_prompt=tool_schema_prompt,
        tool_history=tool_history,
        last_tool_result=last_tool_result,
        todos=todos,
        current_todo_index=state.get("current_todo_index", 0) or 0,
        plan_content=plan_content,
        parent_chain_messages=parent_chain_messages,
        current_conversation_messages=current_conversation_messages,
        last_error=state.get("last_error"),
    )

    # 🔧 关键：在底部注入任务主题
    # 把当前任务追加到 context_prompt 末尾
    task_injection = f"\n\n## 当前任务\n当前任务是向用户回复（主题：{chat_topic}）"

    console.info(f"[chat] 使用完整提示词，注入主题: {chat_topic}")
    console.info(f"[chat] tool_history: {len(tool_history)} 条, conversation: {len(current_conversation_messages)} 条")

    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", SegmentType.CHAT_START, {
            "task_description": f"输出最终回复: {chat_topic}",
            "is_start": True
        })

    try:
        def chat_token_callback(token: str):
            if send_message:
                send_message(token, SegmentType.CHAT_DELTA, {
                    "task_description": f"输出最终回复: {chat_topic}",
                    "is_delta": True
                })

        # 构建消息：使用注入任务主题的 context_prompt
        messages = [{"role": "user", "content": context_prompt + task_injection}]

        # 记录日志
        import datetime
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        with open_trace_log() as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] === CHAT TOOL REQUEST (DIRECTOR) ===\n")
            f.write(f"[{timestamp}] Topic: {chat_topic}\n")
            f.write(f"[{timestamp}] Tool history: {len(tool_history)} items\n")
            f.write(f"\n[{'='*40} SYSTEM PROMPT {'='*40}\n")
            f.write(system_prompt if system_prompt else "(empty)")
            f.write(f"\n[{'='*40} USER MESSAGE + TASK INJECTION {'='*40}\n")
            f.write(context_prompt + task_injection)
            f.write(f"\n{'='*80}\n")
            f.flush()

        result = ""
        chat_system_prompt = _build_chat_system_prompt(settings_service)
        for chunk in llm_service.chat_stream(messages, chat_system_prompt, chat_token_callback):
            result += chunk

        console.success("对话回复完成")

        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": f"输出最终回复: {chat_topic}",
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": f"输出最终回复: {chat_topic}",
                "is_end": True,
                "error": str(e)
            })
        return {"result": f"对话回复失败: {e}", "error": str(e)}


def create_execute_node(llm_service=None, token_callback=None, settings_service=None, message_context=None):
    director_definition = get_definition("director_agent")
    director_base = ReActAgentBase(definition=director_definition)
    
    # 注入 Director Agent 特殊策略（覆盖默认的子Agent实现）
    # 这是策略模式的核心：不同Agent可以使用不同的工具执行策略
    director_base.thinking_strategy = _director_thinking_strategy
    director_base.chat_strategy = _director_chat_strategy
    
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
            tool_name = pending_tools[0].get("tool_name")
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

            enhanced_tool_args = director_base.memory_manager.inject_memory(
                tool_args=tool_args,
                state=state,
                memory_mode=director_base.definition.meta.memory_mode
            )
            
            if enhanced_tool_args.get("previous_results"):
                console.info(f"[ReActAgentBase] ✅ 已注入 {len(enhanced_tool_args['previous_results'])} 条历史记录到 {tool_name}")

            enhanced_message_context = dict(message_context) if message_context else {}
            enhanced_message_context["parent_chain_messages"] = parent_chain_messages
            enhanced_message_context["current_conversation_messages"] = current_conversation_messages
            
            tool_result = run_tool_execution(
                tool_name=tool_name,
                tool_args=enhanced_tool_args,
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

            result_str = str(tool_result.get("result", "") if tool_result.get("result") is not None else "")
            console.box("工具执行结果", result_str)

            new_tool_history = state.get("tool_history", []) + [{
                "tool_name": tool_name,
                "args": tool_args,
                "result": tool_result.get("result"),
                "error": tool_result.get("error"),
                "timestamp": datetime.datetime.now().isoformat(),
            }]

            new_current_conv_msgs = list(current_conversation_messages)
            tool_error = tool_result.get("error")
            content = f"[工具执行: {tool_name}]\n结果: {result_str}"
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
                    "next_action": None,
                }
                if tool_success and tool_name == "update_todo":
                    direct_update.update(build_todo_agent_state_update(
                        todos=tool_args.get("todos"),
                        doing_idx=tool_args.get("doingIdx"),
                    ))
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

        # 当 pending_tools 为空时，返回 final_reply 以打破循环
        # 否则如果没有 final_reply，路由函数会继续循环
        return {
            "pending_tools": [],
            "in_plan_mode": False,
            "execution_mode": None,
            "has_tool_use": False,
            # 添加 final_reply 防止空状态循环
            "final_reply": state.get("last_tool_result") or "任务执行完成",
        }
    
    return execute_node


def route_after_todo_review(_state: AgentState) -> str:
    # 防止 execute 返回空状态后循环回到 decide
    # 如果 iteration_count 已经很高，应该终止
    iteration_count = _state.get("iteration_count", 0) or 0
    max_iterations = _state.get("max_iterations", 10) or 10
    if iteration_count >= max_iterations:
        return "error_summary"
    return "decide"


def route_after_execute(state: AgentState) -> str:
    """路由 after_execute"""
    # 【调试日志】诊断 execute 后路由路径
    _pt = state.get("pending_tools")
    _fr = state.get("final_reply")
    _ln = state.get("last_tool_name")
    _na = state.get("next_action") or {}
    console.debug(f"[route_after_execute] ENTER | tool={_pt is not None and len(_pt) if isinstance(_pt, list) else 'N/A'}, "
                  f"pending_type={type(_pt).__name__}, pending_val={repr(_pt)[:100]}, "
                  f"final_reply={_fr is not None}, last_tool={_ln}, next_action_kind={_na.get('kind')}")

    # 【修复】优先检查 final_reply，确保 chat 工具执行后直接结束
    if state.get("final_reply"):
        console.debug("[route_after_execute] → done (final_reply)")
        return "done"

    # 检查 pending_tools
    if not state.get("pending_tools"):
        console.debug(f"[route_after_execute] → decide (pending_tools empty, tool={_ln})")
        return "decide"

    next_action = state.get("next_action") or {}
    if next_action.get("kind") == "enter_plan":
        console.debug("[route_after_execute] → analyze (enter_plan)")
        return "analyze"

    if state.get("pending_tools"):
        console.debug("[route_after_execute] → execute (has pending)")
        return "execute"

    console.debug("[route_after_execute] → decide (fallback)")
    return "decide"


def _director_post_execute_hook(direct_update: dict, tool_result: dict, state: dict) -> dict:
    from .decision.complexity_analyzer import ExecutionMode
    
    hook_updates = {}
    tool_name = direct_update.get("last_tool_name")
    tool_success = direct_update.get("last_tool_success")
    
    if not tool_success:
        return hook_updates
    
    if tool_name == "update_todo":
        pending_tools = state.get("pending_tools") or []
        assert pending_tools, "update_todo requires a pending tool call"
        tool_args = pending_tools[0].get("args")
        assert isinstance(tool_args, dict), "update_todo requires tool arguments"
        assert "todos" in tool_args and "doingIdx" in tool_args, "update_todo arguments are incomplete"
        hook_updates.update(build_todo_agent_state_update(
            todos=tool_args["todos"],
            doing_idx=tool_args["doingIdx"],
        ))
    
    if tool_name == "switch_execution_mode":
        mode_value = tool_result.get("execution_mode")
        if mode_value == "PLAN":
            hook_updates.update({
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
            hook_updates.update({
                "execution_mode": ExecutionMode.DIRECT,
                "mode_reason": tool_result.get("mode_reason") or "agent 维持 DIRECT",
            })
    
    return hook_updates


def create_orchestrator_graph_v3(llm_service=None, token_callback=None, memory_mode: str = "accumulate", window_size: int = 3, settings_service=None, message_context=None):
    from .definitions import get_definition
    from langgraph.graph import StateGraph, END
    
    definition = get_definition("director_agent")
    director_base = ReActAgentBase(definition=definition)
    
    graph = StateGraph(AgentState)

    graph.add_node("analyze", create_analyze_node(llm_service, message_context, settings_service))
    
    loop_config = {
        "enable_todo": True,
        "post_execute_hook": _director_post_execute_hook,
        "llm_service": llm_service,
        "settings_service": settings_service,
        "message_context": message_context,
    }
    
    loop_subgraph = director_base.build_react_loop_graph(loop_config)
    
    graph.add_node("plan", create_plan_node(llm_service, token_callback, settings_service, message_context))

    graph.set_entry_point("analyze")

    graph.add_conditional_edges("analyze", route_after_analyze, {
        "decide": "decide",
        "execute": "execute",
        "done": END
    })

    graph.add_node("decide", director_base._create_decide_node(
        llm_service=llm_service,
        settings_service=settings_service,
        message_context=message_context,
    ))

    graph.add_node("check_state", check_state_node)

    graph.add_node("execute", director_base._create_execute_node(
        llm_service=llm_service,
        settings_service=settings_service,
        message_context=message_context,
        post_execute_hook=_director_post_execute_hook,
    ))
    
    graph.add_node("todo_review", director_base._create_todo_review_node(
        llm_service=llm_service,
        message_context=message_context,
    ))

    graph.add_node("error_summary", director_base._create_error_summary_node(
        llm_service=llm_service,
        message_context=message_context,
    ))

    # decide → check_state(节点) → _route_after_check_state(edge) → 目标
    graph.add_edge("decide", "check_state")

    graph.add_conditional_edges("check_state", _route_after_check_state, {
        "analyze": "analyze",
        "decide": "decide",
        "execute": "execute",
        "done": END,
        "error_summary": "error_summary"
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

    graph.add_edge("error_summary", END)

    graph.add_conditional_edges("plan", lambda s: END, {END: END})

    # ⚠️ 注意：StateGraph.compile() 不支持 recursion_limit 参数！
    # 如需限制递归深度，应在 check_state_v3 中通过 iteration_count 检查来控制
    return graph.compile(
        interrupt_before=None,
        debug=False,
    )


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
    current_conversation_messages: List[dict] = None,
    prior_agent_state: Optional[AgentState] = None,
) -> dict:
    print("\n" + "="*60)
    print("[Director Agent] 块类型驱动循环 + Plan/Execute 分离")
    print(f"[Director Agent] 记忆模式: {memory_mode}, 窗口大小: {window_size}")
    print("="*60)

    # 从 AgentDefinition 读取配置
    definition = get_definition("director_agent")
    print(f"[Director Agent] max_iterations: {definition.meta.max_iterations}")

    initial_state = build_initial_state(
        user_message=user_message,
        workspace_id=workspace_id,
        definition=definition,
        parent_chain_messages=parent_chain_messages,
        current_conversation_messages=current_conversation_messages,
        is_root_graph=True,
        prior_agent_state=prior_agent_state,
    )

    graph = create_orchestrator_graph_v3(llm_service, token_callback, memory_mode, window_size, settings_service, message_context)

    # ✅ 正确做法：通过 graph.invoke() 的 config 参数传递 recursion_limit
    # ⚠️ compile() 不接受此参数！必须在 invoke() 时传入
    _max_iters = definition.meta.max_iterations
    graph_config = {'recursion_limit': calculate_recursion_limit(_max_iters)}

    final_state = graph.invoke(initial_state, config=graph_config)

    print("\n" + "="*60)
    print("[Director Agent] 主编排图执行完成")
    print("="*60)

    return final_state


run_graph_v2 = run_graph_v3
create_orchestrator_graph_v2 = create_orchestrator_graph_v3
check_state_v2 = check_state_v3
