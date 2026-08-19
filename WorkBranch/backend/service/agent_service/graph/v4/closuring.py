"""sidekick-closuring 节点（V4 定稿）。

判定目标单一：快速模型只判断 leader 是否在完成工作后输出了 text 总结。
- 通过 -> finalize（done）；
- 不通过 -> 注入 <closur-feedback> 回 leader-reasoning 继续；
- 预算 agent:closure_max_rounds（默认 8）：每次进入 +1，超过计数后
  即使条件满足也不再回 reasoning，直接 finalize（done）。
异常终止路径（固定模板）不经过本节点。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ...state import AgentState
from core.logging import console, open_trace_log


CLOSURING_PROMPT = """你是一个收尾校验助手。判断 leader 是否已经完成用户任务，并在工作最后输出了 text 总结。

判定标准（必须同时满足）：
1. leader 已经通过工具获取了完成任务所需的材料，或任务确实无法继续并已说明原因；
2. leader 已经输出 type=text 的最终总结文本，内容对用户问题给出了结论；
3. 如果任务没有完成，或缺少 text 总结，都必须判定为未通过。

输出（严格 JSON）：
{"passed": true/false, "reason": "一句话理由", "feedback": "未通过时给 leader 的改进提示（不超过 120 字）"}
"""


_TOOL_HISTORY_FIELD_LIMIT = 100


def _serialize_tool_history_field(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _clip_tool_history_field(*, value, field: str, record: dict) -> str:
    serialized = _serialize_tool_history_field(value)
    if len(serialized) > _TOOL_HISTORY_FIELD_LIMIT:
        console.warning(
            "[sidekick-closuring] tool history field truncated "
            f"tool={record.get('tool_name')} call_seq={record.get('call_seq')} "
            f"field={field} original_length={len(serialized)} "
            f"limit={_TOOL_HISTORY_FIELD_LIMIT}"
        )
    return serialized[:_TOOL_HISTORY_FIELD_LIMIT]


def _max_rounds(settings_service, default: int = 8) -> int:
    if settings_service is None:
        return default
    try:
        return max(1, int(settings_service.get("agent:closure_max_rounds") or default))
    except Exception:
        return default


def _enabled(settings_service) -> bool:
    if settings_service is None:
        return False
    try:
        return bool(settings_service.get("agent:closuring_enabled"))
    except Exception:
        return False


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            start = text.find("{")
            end = text.rfind("}")
            data = json.loads(text[start : end + 1]) if start != -1 and end > start else None
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _build_feedback_check_prompt(state: AgentState) -> str:
    final_text = state.get("pending_final_text") or state.get("final_reply") or ""
    records = []
    for r in state.get("tool_records") or []:
        if isinstance(r, dict) and r.get("call_seq") is not None:
            result = r.get("result")
            if result is None:
                result = r.get("error") or ""
            request_text = _clip_tool_history_field(
                value=r.get("args") or {},
                field="request",
                record=r,
            )
            result_text = _clip_tool_history_field(
                value=result,
                field="result",
                record=r,
            )
            records.append(
                f"call_seq={r.get('call_seq')} {r.get('tool_name')} "
                f"status={r.get('status')} request={request_text} result={result_text}"
            )
    user_question = state.get("current_user_message_text") or state.get("user_message") or ""
    return (
        f"用户问题：{user_question}\n\n"
        f"leader 的 text 总结：\n{final_text[:2000]}\n\n"
        f"工具执行记录：\n" + ("\n".join(records[-20:]) or "（无）") + "\n\n"
        "请按判定标准输出 JSON。"
    )


def create_closuring_node(llm_service=None, settings_service=None, message_context=None):
    def closuring_node(state: AgentState) -> dict:
        if not _enabled(settings_service):
            return {"_route_target": "finalize"}

        closure_rounds = (state.get("closure_rounds", 0) or 0) + 1
        budget = _max_rounds(settings_service)

        # 预算耗尽：即使条件满足也不再回 reasoning，直接 done
        if closure_rounds > budget:
            console.warning(
                f"[sidekick-closuring] 预算耗尽 ({closure_rounds}/{budget})，强制 finalize"
            )
            return {"closure_rounds": closure_rounds, "_route_target": "finalize"}

        # fastllm 判定
        try:
            from service.agent_service.service.llm_service import FastLLMService
            fast = FastLLMService(settings_service)
            response = fast.chat(
                messages=[
                    {"role": "user", "content": _build_feedback_check_prompt(state)}
                ],
                system_prompt=CLOSURING_PROMPT,
            )
            data = _extract_json(response) or {}
            passed = bool(data.get("passed"))
            feedback = str(data.get("feedback") or "")
            reason = str(data.get("reason") or "")
        except Exception as e:
            console.warning(f"[sidekick-closuring] 判定异常，按通过处理: {e}")
            return {"closure_rounds": closure_rounds, "_route_target": "finalize"}

        try:
            with open_trace_log() as f:
                f.write(
                    f"[closuring] round={closure_rounds} passed={passed} "
                    f"reason={reason} feedback={feedback}\n"
                )
                f.flush()
        except Exception:
            pass

        if passed:
            return {"closure_rounds": closure_rounds, "_route_target": "finalize"}

        # 不通过：注入 feedback，清空待定最终文本，回 reasoning 继续
        return {
            "closure_rounds": closure_rounds,
            "closur_feedback": feedback or "你还没有在工作最后使用 text 进行总结反馈，需要先完成总结再 done。",
            "pending_final_text": None,
            "final_reply": None,
            "_route_target": "reasoning",
        }

    return closuring_node


def route_after_closuring(state: AgentState) -> str:
    return state.get("_route_target") or "finalize"
