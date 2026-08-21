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


CLOSURING_PROMPT = """你是一个收尾行为校验助手。你只审查与业务内容无关、可客观验证的 Agent 行为，不审查业务结果质量。

检查范围仅限：
1. Director 是否输出了面向用户的 type=text 最终回复。final_reply_present=true 即满足；不读取或评价最终回复正文，不管回复写了什么。
2. 用户明确要求生成某种产物文件时，成功的工具行为是否表明该文件类型已经生成；不检查文件内部内容、排版或质量。
3. 用户明确要求执行外部动作时，成功的工具行为是否表明该动作已经执行；不检查动作产生的业务结果质量。
4. 如果 Director 可见的工具描述能证明目标路径不可达或工具不支持该文件类型或外部动作，且 final_reply_present=true，则对应要求视为满足；不得检查回复是否解释了限制。

只有用户明确提出的文件或外部动作要求才能作为拒绝理由，不得把阅读、搜索、分析、计算、预测、回答质量或信息充分程度解释成产物或外部动作要求。
禁止审查事实准确性、业务内容完整性、分析质量、预测合理性、证据充分度、读取范围及文件内部内容是否正确。如果只有业务问题，必须通过。

输出严格 JSON。passed=false 时 failure_kind 只能是以下三种之一：
- missing_final_reply
- required_artifact_not_generated
- required_external_action_not_executed

不存在上述行为问题时必须输出：
{"passed": true, "failure_kind": "none", "reason": "程序性收尾行为已完成", "feedback": ""}

存在行为问题时输出：
{"passed": false, "failure_kind": "上述三种之一", "reason": "只描述缺失的可观察行为", "feedback": "只要求补做该行为，不得要求修改业务内容"}
"""


_TOOL_HISTORY_FIELD_LIMIT = 100

_ALLOWED_MODEL_FAILURE_KINDS = {
    "required_artifact_not_generated",
    "required_external_action_not_executed",
}

_TOOL_PAYLOAD_FIELDS = {
    "body",
    "code",
    "command",
    "content",
    "context",
    "data",
    "description",
    "feedback",
    "input",
    "instructions",
    "message",
    "metadata",
    "options",
    "pattern",
    "prompt",
    "query",
    "question",
    "raw",
    "remark",
    "sql",
    "summary",
    "task",
    "text",
    "title",
}


def _serialize_tool_history_field(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _behavior_only_tool_args(value):
    if isinstance(value, dict):
        return {
            key: _behavior_only_tool_args(item)
            for key, item in value.items()
            if str(key).lower() not in _TOOL_PAYLOAD_FIELDS
        }
    if isinstance(value, list):
        return [_behavior_only_tool_args(item) for item in value]
    return value


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
    final_reply_present = (
        state.get("pending_final_text") is not None
        or state.get("final_reply") is not None
    )
    records = []
    for r in state.get("tool_records") or []:
        if isinstance(r, dict) and r.get("call_seq") is not None:
            request_text = _clip_tool_history_field(
                value=_behavior_only_tool_args(r.get("args") or {}),
                field="request",
                record=r,
            )
            records.append({
                "call_seq": r.get("call_seq"),
                "tool_name": r.get("tool_name"),
                "status": r.get("status"),
                "request": request_text,
            })
    user_question = state.get("current_user_message_text") or state.get("user_message") or ""
    tool_schema = build_agent_tool_schema("director_agent", settings_service)
    behavior_facts = {
        "final_reply_present": final_reply_present,
        "tool_records": records[-20:],
    }
    return (
        "用户原始要求（仅用于识别明确要求的文件类型和外部动作）：\n"
        f"{user_question}\n\n"
        "Director 可观察行为事实（不含回复正文和工具结果正文）：\n"
        f"{json.dumps(behavior_facts, ensure_ascii=False, indent=2)}\n\n"
        "Director 可见的工具描述（Director 决策时能看到以下描述）：\n"
        f"{tool_schema}\n\n"
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

        has_final_reply = (
            state.get("pending_final_text") is not None
            or state.get("final_reply") is not None
        )
        if not has_final_reply:
            return {
                "closure_rounds": closure_rounds,
                "closur_feedback": "结束前必须先输出面向用户的 type=text 最终回复。",
                "pending_final_text": None,
                "final_reply": None,
                "_route_target": "reasoning",
            }

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
            failure_kind = str(data.get("failure_kind") or "none")
            feedback = str(data.get("feedback") or "")
            reason = str(data.get("reason") or "")
        except Exception as e:
            console.warning(f"[sidekick-closuring] 判定异常，按通过处理: {e}")
            return {"closure_rounds": closure_rounds, "_route_target": "finalize"}

        if not passed and failure_kind not in _ALLOWED_MODEL_FAILURE_KINDS:
            console.warning(
                "[sidekick-closuring] ignored out-of-scope rejection "
                f"failure_kind={failure_kind} reason={reason}"
            )
            passed = True

        try:
            with open_trace_log() as f:
                f.write(
                    f"[closuring] round={closure_rounds} passed={passed} "
                    f"failure_kind={failure_kind} reason={reason} feedback={feedback}\n"
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
