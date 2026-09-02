"""
leader-acting 节点（V4 定稿）

职责：收到 pending_batch（{reason, calls[]}）→ 批次内并行执行（同步 per-call，
     子代理类工具串行）→ 等待全部完成 → 写 tool_records（round + call_seq + reason）
     → 应用 todo 更新 → iteration_count + 1 → 回 reasoning。

约定：
- 解析/语义错误不会到达本节点（由 reasoning 内部消化）；
- 本节点只产生执行结果（成功/失败记录），失败详情以 tool_records 为唯一事实源；
- 不做 execute 自循环：一批调用一次 acting 全部执行完毕。
"""

from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from ...state import AgentState
from core.logging import console
from singleton import get_workspace_service
from ..subgraphs.tool_registry import ask_user_question_enabled


SUBAGENT_TOOLS = {
    "call_explore_agent",
    "call_review_agent",
    "call_prediction_agent",
    "call_plan_agent",
}
INTERACTIVE_TOOLS = {"ask_user_question"}


def _tool_valid_params(tool_name: str) -> list:
    """从工具协议 ALL_TOOLS 提取该工具的合法参数名清单（与提示词同源），用于失败回执纠错"""
    try:
        from ...tools import ALL_TOOLS
        meta = ALL_TOOLS.get(tool_name)
    except Exception:
        meta = None
    if not meta or not meta.get("params"):
        return []
    params_text = meta["params"]
    brace = params_text.find("{")
    if brace < 0:
        return []
    import re as _re
    names = _re.findall(r"\"([A-Za-z_][A-Za-z0-9_]*)\"", params_text[brace:])
    seen: list = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen


def _auto_approve(settings_service) -> bool:
    if settings_service is None:
        return False
    try:
        return bool(settings_service.get("agent:ask_user_auto_approve"))
    except Exception:
        return False


def _parallelism(settings_service, default: int = 3) -> int:
    if settings_service is None:
        return default
    try:
        return max(1, int(settings_service.get("agent:tool_parallelism") or default))
    except Exception:
        return default


def _execute_single_call(
    call: dict,
    *,
    workspace_id: str,
    state: AgentState,
    workspace_service,
    llm_service,
    token_callback,
    settings_service,
    message_context: Optional[dict],
) -> dict:
    """执行单个工具调用，返回带 call_seq 的结果记录。"""
    call_seq = call.get("call_seq")
    tool_name = call.get("tool_name", "")
    tool_args = call.get("tool_args") or {}
    task_description = call.get("task_description") or call.get("reason") or ""
    started = time.perf_counter()

    base_record = {
        "call_seq": call_seq,
        "tool_name": tool_name,
        "args": tool_args,
        "task_description": task_description,
    }

    # chat 工具已退役：直接输出 text，不经过工具
    if tool_name == "chat":
        return {
            **base_record,
            "status": "failed",
            "error": "chat 工具已退役，请改用 type=text 输出最终总结",
            "duration_ms": 0,
            "timestamp": datetime.datetime.now().isoformat(),
        }

    if tool_name in INTERACTIVE_TOOLS:
        if not ask_user_question_enabled(settings_service):
            return {
                **base_record,
                "status": "failed",
                "error": "ask_user_question 已停用（agent.ask_user_question_enabled=false），请改用 type=text 输出问题或阻塞说明",
                "duration_ms": 0,
                "timestamp": datetime.datetime.now().isoformat(),
            }
        if _auto_approve(settings_service):
            return {
                **base_record,
                "status": "success",
                "result": "自动批准",
                "duration_ms": 0,
                "timestamp": datetime.datetime.now().isoformat(),
            }
        # LangGraph interrupt：图暂停，等待用户经 resume 端点回复
        from langgraph.types import interrupt
        answer = interrupt({
            "type": "ask_user_question",
            "call_seq": call_seq,
            "question": tool_args.get("question") or "",
            "options": tool_args.get("options") or [],
            "context": tool_args.get("context") or "",
        })
        if answer is None:
            answer = ""
        return {
            **base_record,
            "status": "success",
            "result": str(answer),
            "duration_ms": 0,
            "timestamp": datetime.datetime.now().isoformat(),
        }

    # 与旧 execute 节点一致：注入记忆 + 消息上下文
    enhanced_args = tool_args
    try:
        from ..react_agent_base import MemoryManager
        memory_mode = (state.get("_definition_meta") or {}).get("memory_mode") if state.get("_definition_meta") else None
        if memory_mode is None:
            memory_mode = "accumulate"
        manager = MemoryManager()
        enhanced_args = manager.inject_memory(
            tool_args=tool_args,
            state=state,
            memory_mode=memory_mode,
        )
    except Exception:
        enhanced_args = tool_args

    if (
        tool_name in SUBAGENT_TOOLS
        and task_description
        and not enhanced_args.get("task_description")
    ):
        enhanced_args = dict(enhanced_args)
        enhanced_args["task_description"] = task_description

    enhanced_message_context = dict(message_context) if message_context else {}
    enhanced_message_context["workspace_id"] = workspace_id
    enhanced_message_context["parent_chain_messages"] = state.get("parent_chain_messages") or []
    enhanced_message_context["current_conversation_messages"] = state.get("current_conversation_messages") or []
    enhanced_message_context["current_user_message_text"] = state.get("current_user_message_text") or ""

    try:
        from ..subgraphs.tool_executor import run_tool_execution
        tool_result = run_tool_execution(
            tool_name=tool_name,
            tool_args=enhanced_args,
            workspace_id=workspace_id,
            previous_calls=_legacy_history(state),
            workspace_service=workspace_service,
            llm_service=llm_service,
            token_callback=token_callback,
            task_description=task_description,
            previous_results=[r.get("result") for r in _legacy_history(state) if r.get("result")],
            agent_type=state.get("agent_type") or "director_agent",
            settings_service=settings_service,
            message_context=enhanced_message_context,
        )
    except Exception as e:
        tool_result = {"result": None, "error": f"{type(e).__name__}: {e}"}

    duration_ms = int((time.perf_counter() - started) * 1000)
    error = tool_result.get("error") if isinstance(tool_result, dict) else f"工具返回异常: {tool_result}"
    if error:
        valid = _tool_valid_params(tool_name)
        if valid:
            error = f"{error}\n[合法参数]: {", ".join(valid)}"
    return {
        **base_record,
        "status": "failed" if error else "success",
        "result": tool_result.get("result") if isinstance(tool_result, dict) else None,
        "error": error,
        "duration_ms": duration_ms,
        "timestamp": datetime.datetime.now().isoformat(),
    }


def _legacy_history(state: AgentState) -> list[dict]:
    """把 V4 tool_records 转成旧 run_tool_execution 期望的 previous_calls 形状。"""
    out = []
    for r in state.get("tool_records") or []:
        if not isinstance(r, dict) or r.get("call_seq") is None:
            continue
        out.append({
            "tool": r.get("tool_name"),
            "args": r.get("args") or {},
            "result": r.get("result") or r.get("error") or "",
        })
    return out


def _apply_todo_update(state: AgentState, results: list[dict]) -> dict:
    """update_todo 成功后更新 todos / current_todo_index（继承旧 post_execute_hook 语义）。"""
    update = {}
    for r in results:
        if r.get("tool_name") != "update_todo" or r.get("status") != "success":
            continue
        try:
            import json
            data = json.loads(r.get("result") or "{}")
        except Exception:
            data = {}
        if data.get("todos") is not None:
            update["todos"] = data["todos"]
        if data.get("doingIdx") is not None:
            update["current_todo_index"] = int(data["doingIdx"])
    return update


def create_acting_node(
    llm_service=None,
    token_callback=None,
    settings_service=None,
    message_context=None,
    post_execute_hook: Optional[Callable] = None,
):
    workspace_service = get_workspace_service()

    def acting_node(state: AgentState) -> dict:
        batch = state.get("pending_batch") or {}
        calls = batch.get("calls") or []
        reason = batch.get("reason", "")
        if not calls:
            console.warning("[v4-acting] pending_batch.calls 为空，直接回 reasoning")
            return {
                "pending_batch": None,
                "acting_failures": [{"status": "failed", "error": "空批次"}],
                "iteration_count": (state.get("iteration_count", 0) or 0) + 1,
                "_route_target": "reasoning",
            }

        round_no = (state.get("iteration_count", 0) or 0) + 1
        workspace_id = state.get("workspace_id", "")

        results: list[dict] = []
        serial_calls = [c for c in calls if c.get("tool_name") in SUBAGENT_TOOLS or c.get("tool_name") in INTERACTIVE_TOOLS]
        parallel_calls = [c for c in calls if c not in serial_calls]

        # 串行：子代理 / 交互工具
        for call in serial_calls:
            results.append(_execute_single_call(
                call,
                workspace_id=workspace_id,
                state=state,
                workspace_service=workspace_service,
                llm_service=llm_service,
                token_callback=token_callback,
                settings_service=settings_service,
                message_context=message_context,
            ))

        # 并行：常规工具
        if parallel_calls:
            max_workers = _parallelism(settings_service)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_execute_single_call, call,
                                workspace_id=workspace_id,
                                state=state,
                                workspace_service=workspace_service,
                                llm_service=llm_service,
                                token_callback=token_callback,
                                settings_service=settings_service,
                                message_context=message_context): call
                    for call in parallel_calls
                }
                for future in as_completed(futures):
                    results.append(future.result())

        # 按 call_seq 排序（保持批次内顺序稳定）
        results.sort(key=lambda r: r.get("call_seq", 0))
        results = [{**result, "round": round_no} for result in results]

        # 写 tool_records（批次头 + 逐条结果）
        tool_records = list(state.get("tool_records") or [])
        tool_records.append({"round": round_no, "reason": reason})
        tool_records.extend(results)

        update: dict[str, Any] = {
            "tool_records": tool_records,
            "iteration_count": round_no,
            "pending_batch": None,
            "has_tool_use": False,
            "pending_tools": [],
            "next_action": None,
            "acting_failures": [r for r in results if r.get("status") == "failed"] or None,
            "_route_target": "reasoning",
        }

        # todo 更新（update_todo 工具）
        todo_update = _apply_todo_update(state, results)
        update.update(todo_update)

        if post_execute_hook:
            try:
                hook_update = post_execute_hook(update, results, state)
                if hook_update:
                    update.update(hook_update)
            except Exception as e:
                console.warning(f"[v4-acting] post_execute_hook 异常: {e}")

        return update

    return acting_node


def route_after_acting(state: AgentState) -> str:
    return state.get("_route_target") or "reasoning"
