"""
leader-reasoning 节点（V4 定稿）

职责：入口闸门检查 → 标签化提示词 → LLM → 解析 + 语义校验（内部重试，绝不进入 acting）
     → 路由：tool_calls → acting；text/done → closuring（未启用时直接 done）。
"""

from __future__ import annotations

from typing import Any, Optional

from ...state import AgentState
from core.logging import console, open_trace_log
from .prompt import (
    build_tagged_prompt,
    fixed_iteration_limit_text,
    fixed_parse_failure_text,
    fixed_tool_loop_text,
)
from .protocol import leader_output_json_schema, validate_leader_output
from ..decision.tool_call_parser import DecisionParseError, parse_leader_output
from ..subgraphs.tool_registry import get_allowed_tools


MAX_DECISION_RETRIES = 3
MAX_RECORD_LOOP = 3


def _fastllm_correction_guidance(
    raw_response: str,
    issue: str,
    allowed_tools: list[str],
    settings_service,
) -> str:
    """解析/语义错误时用快速模型生成修正性提示词（提高重试效果）。

    fastllm 失败时返回空串（退化为纯错误信息 + 原文，不影响主流程）。
    """
    try:
        from service.agent_service.service.llm_service import FastLLMService
        fast = FastLLMService(settings_service)
        prompt = (
            "下面是 agent 的一轮错误输出，请给出修正建议（不超过 120 字，"
            "说明正确输出形态，例如应使用哪个工具、补哪个字段、修正哪个 call_seq）。\n"
            f"错误: {issue}\n"
            f"可用工具: {', '.join(allowed_tools[:30])}\n"
            f"原始输出: {str(raw_response)[:1500]}"
        )
        resp = fast.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是输出协议校验助手，只输出修正建议文本，不要输出 JSON。",
        )
        guidance = str(resp or "").strip()
        return guidance[:300]
    except Exception:
        return ""


def _terminal_update(text: str, state: AgentState) -> dict:
    """异常终止：设置待定最终文本，路由 finalize（跳过 closuring）。"""
    return {
        "pending_final_text": text,
        "final_reply": text,
        "has_tool_use": False,
        "pending_tools": [],
        "pending_batch": None,
        "next_action": None,
        "last_error": None,
        "parse_error": None,
        "closur_feedback": None,
        "_route_target": "finalize",
    }


def _detect_tool_failure_loop(tool_records: list[dict]) -> Optional[tuple[str, int]]:
    """检测同工具连续失败循环（近 5 条记录中连续失败 >=3 或 >=4/5）。"""
    records = [r for r in tool_records if isinstance(r, dict) and r.get("call_seq") is not None]
    if len(records) < 4:
        return None
    tail = records[-5:]
    # 连续同工具失败
    last_name = tail[-1].get("tool_name")
    repeat = 0
    for r in reversed(tail):
        if r.get("tool_name") == last_name and r.get("status") == "failed":
            repeat += 1
        else:
            break
    if repeat >= MAX_RECORD_LOOP:
        return (last_name, repeat)
    # 近 5 条 >=4 失败
    recent_failures = sum(1 for r in tail if r.get("status") == "failed")
    if recent_failures >= 4 and len(tail) >= 5:
        return ("(多个工具)", recent_failures)
    return None


def _recent_results(tool_records: list[dict]) -> list[str]:
    return [
        str(r.get("result") or "")
        for r in tool_records
        if isinstance(r, dict) and r.get("call_seq") is not None and r.get("result")
    ]


def _write_reasoning_trace(agent_type: str, round_no: int, user_message: str, raw_response: str) -> None:
    try:
        with open_trace_log() as f:
            f.write(f"\n[{round_no}] === V4 REASONING REQUEST ===\n")
            f.write(f"agent_type={agent_type} round={round_no}\n")
            f.write(f"--- USER (includes <system>, {len(user_message)} chars) ---\n{user_message}\n")
            f.write(f"--- RAW RESPONSE ({len(raw_response)} chars) ---\n{raw_response}\n")
            f.write("=== V4 REASONING END ===\n\n")
            f.flush()
    except Exception:
        pass


def create_reasoning_node(llm_service=None, settings_service=None, message_context=None):
    def reasoning_node(state: AgentState) -> dict:
        agent_type = state.get("agent_type") or "director_agent"
        user_message = state.get("current_user_message_text") or state.get("user_message") or ""
        workspace_id = state.get("workspace_id", "")
        iteration_count = state.get("iteration_count", 0) or 0
        max_iterations = state.get("max_iterations", 32) or 32
        tool_records = state.get("tool_records") or []

        # ===== 入口闸门：工具失败循环 =====
        loop = _detect_tool_failure_loop(tool_records)
        if loop:
            console.warning(f"[v4-reasoning] 工具失败循环: {loop}")
            text = fixed_tool_loop_text(loop[0], loop[1], _recent_results(tool_records))
            return _terminal_update(text, state)

        # ===== 入口闸门：轮次上限 =====
        if iteration_count >= max_iterations:
            console.warning(f"[v4-reasoning] 轮次已达上限 {iteration_count}/{max_iterations}")
            text = fixed_iteration_limit_text(max_iterations, _recent_results(tool_records))
            return _terminal_update(text, state)

        # ===== 入口闸门：解析/语义重试超限 =====
        decision_error_count = state.get("decision_error_count", 0) or 0
        if decision_error_count >= MAX_DECISION_RETRIES:
            last_detail = state.get("parse_error") or "多次解析失败"
            text = fixed_parse_failure_text(
                last_detail[:500],
                state.get("parse_error_raw") or "",
            )
            return _terminal_update(text, state)

        if llm_service is None:
            reply = "无法自动决策下一步：LLM 服务未配置。"
            return _terminal_update(reply, state)

        # ===== 组装标签化提示词 =====
        system_prompt_override = None
        if agent_type != "director_agent":
            try:
                from ..definitions import get_definition
                _def = get_definition(agent_type)
                _base = getattr(getattr(_def, "prompt", None), "system_prompt", None)
                if _base:
                    system_prompt_override = _base
            except Exception:
                system_prompt_override = None

        _, user_message_text = build_tagged_prompt(
            agent_type=agent_type,
            user_message=user_message,
            workspace_id=workspace_id,
            round_no=iteration_count + 1,
            max_iterations=max_iterations,
            tool_records=tool_records,
            todos=state.get("todos") or [],
            current_todo_index=state.get("current_todo_index", 0) or 0,
            plan_content=state.get("plan_content"),
            parent_chain_messages=state.get("parent_chain_messages") or [],
            current_conversation_messages=state.get("current_conversation_messages") or [],
            parse_error=state.get("parse_error"),
            closur_feedback=state.get("closur_feedback"),
            acting_failures=state.get("acting_failures"),
            settings_service=settings_service,
            message_context=message_context,
            system_prompt_override=system_prompt_override,
        )

        # ===== LLM 调用（结构化输出 auto：json_schema -> 400 降级 json_object）=====
        raw_response = ""
        try:
            chat_method = getattr(llm_service, "chat_with_structured_output", None)
            if chat_method is not None:
                raw_response = chat_method(
                    messages=[{"role": "user", "content": user_message_text}],
                    system_prompt=None,
                    schema=leader_output_json_schema(),
                )
            else:
                raw_response = llm_service.chat_with_json_mode(
                    messages=[{"role": "user", "content": user_message_text}],
                    system_prompt=None,
                )
            raw_response = str(raw_response or "").strip()
            if not raw_response:
                raise ValueError("LLM 返回了空响应")
        except Exception as e:
            raw_response = str(getattr(e, "response_text", "")) if not raw_response else raw_response
            decision_error_count += 1
            if decision_error_count >= MAX_DECISION_RETRIES:
                text = fixed_parse_failure_text(f"LLM 调用失败: {e}", raw_response)
                return _terminal_update(text, state)
            return {
                "decision_error_count": decision_error_count,
                "parse_error": f"类别: llm_call\n说明: {e}\n原文: {raw_response}",
                "parse_error_raw": raw_response,
                "_route_target": "reasoning",
            }

        _write_reasoning_trace(agent_type, iteration_count + 1, user_message_text, raw_response)

        # ===== 解析（容错链）=====
        try:
            parsed = parse_leader_output(raw_response)
        except DecisionParseError as e:
            decision_error_count += 1
            if decision_error_count >= MAX_DECISION_RETRIES:
                text = fixed_parse_failure_text(f"[{e.category}] {e}", raw_response)
                return _terminal_update(text, state)
            guidance = _fastllm_correction_guidance(
                raw_response, f"[{e.category}] {e}", [], settings_service
            )
            extra = f"\n修正建议: {guidance}" if guidance else ""
            return {
                "decision_error_count": decision_error_count,
                "parse_error": f"类别: {e.category}\n说明: {e}\n原文: {raw_response}{extra}",
                "parse_error_raw": raw_response,
                "_route_target": "reasoning",
            }

        # ===== 语义校验（hard 层）=====
        allowed_tools = get_allowed_tools(agent_type, settings_service)
        issues = validate_leader_output(parsed, set(allowed_tools))
        if issues:
            decision_error_count += 1
            issue_text = "；".join(issues)
            if decision_error_count >= MAX_DECISION_RETRIES:
                text = fixed_parse_failure_text(f"语义校验失败: {issue_text}", raw_response)
                return _terminal_update(text, state)
            guidance = _fastllm_correction_guidance(
                raw_response, issue_text, allowed_tools, settings_service
            )
            extra = f"\n修正建议: {guidance}" if guidance else ""
            return {
                "decision_error_count": decision_error_count,
                "parse_error": f"类别: semantic\n说明: {issue_text}\n原文: {raw_response}{extra}",
                "parse_error_raw": raw_response,
                "_route_target": "reasoning",
            }

        # ===== 成功：路由 =====
        otype = parsed.get("type")
        if otype == "tool_calls":
            return {
                "pending_batch": parsed.get("content") or {},
                "parse_error": None,
                "parse_error_raw": None,
                "closur_feedback": None,
                "decision_error_count": 0,
                "acting_failures": None,
                "_route_target": "acting",
            }

        # text / done
        final_text = str(parsed.get("content") or "")
        return {
            "pending_final_text": final_text,
            "final_reply": final_text,
            "output_type": otype,
            "has_tool_use": False,
            "pending_tools": [],
            "pending_batch": None,
            "next_action": None,
            "parse_error": None,
            "parse_error_raw": None,
            "closur_feedback": None,
            "decision_error_count": 0,
            "_route_target": "closuring" if _closuring_enabled(settings_service) else "finalize",
        }

    return reasoning_node


def _closuring_enabled(settings_service) -> bool:
    if settings_service is None:
        return False
    try:
        return bool(settings_service.get("agent:closuring_enabled"))
    except Exception:
        return False


def route_after_reasoning(state: AgentState) -> str:
    """reasoning 路由：读 _route_target。"""
    return state.get("_route_target") or "reasoning"
