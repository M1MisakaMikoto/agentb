"""sidekick-closuring 节点（V4 定稿）。

判定目标：快速模型只审查业务内容无关、可客观验证的收尾行为。
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
from .prompt import build_agent_tool_schema


CLOSURING_PROMPT = """你是一个收尾校验助手。你只审查与业务内容无关、可客观验证的收尾行为，不审查业务结果质量。

检查范围：
1. Director 是否在结束前输出了面向用户的 type=text 最终回复；
2. 用户明确要求生成文件或执行外部动作时，工具记录是否表明该程序性要求已执行；
3. 用户明确要求产物文件类型时，Director 是否至少尝试生成该文件类型。例如用户要求 PDF，Director 未尝试生成 PDF 而只生成 Markdown，应判定为未通过；
4. 仅当工具记录或 Director 可见的工具描述能证明目标路径不可达、工具不支持或操作失败，且 Director 已在最终回复中说明限制和已完成的可行部分时，才可放宽对应要求。

格式只指用户明确要求的产物文件类型。不要检查回复或文件的排版、章节、字数、字段、文本结构及其他内容格式。
禁止审查事实准确性、业务内容完整性、分析质量、预测合理性、证据充分度及文件内部内容是否正确；这些均由 Director 负责。

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


def _build_feedback_check_prompt(state: AgentState, settings_service=None) -> str:
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
    tool_schema = build_agent_tool_schema("director_agent", settings_service)
    return (
        f"用户问题：{user_question}\n\n"
        f"leader 的 text 总结：\n{final_text[:2000]}\n\n"
        "Director 可见的工具描述（Director 决策时能看到以下描述）：\n"
        f"{tool_schema}\n\n"
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
                    {
                        "role": "user",
                        "content": _build_feedback_check_prompt(state, settings_service),
                    }
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
