"""
V4 输出协议（定稿，见 docs/agentb-report-merged.html §3.3）

leader 的每一轮输出必须是以下三种之一：
    { "type": "tool_calls", "content": { "reason": "...", "calls": [ {call_seq, tool_name, tool_args, task_description}, ... ] } }
    { "type": "text",       "content": "..." }
    { "type": "done",       "content": null }

- tool_calls 支持 1..N 个并行调用；call_seq 批次内唯一；
- content 中不承载任何图路由字段（kind 等已移除）；
- 解析/语义校验失败由 reasoning 内部处理，绝不进入 acting。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class ToolCallSpec(BaseModel):
    """单个工具调用规格（批次内 call_seq 唯一）。"""

    model_config = ConfigDict(extra="allow")

    call_seq: int = Field(ge=0, le=100000)
    tool_name: str = Field(min_length=1)
    tool_args: dict[str, Any] = Field(default_factory=dict)
    task_description: Optional[str] = None


class ToolCallsContent(BaseModel):
    """tool_calls 的 content：reason（批次目的）+ calls[]（并行调用）。"""

    model_config = ConfigDict(extra="allow")

    reason: str = ""
    calls: list[ToolCallSpec] = Field(min_length=1)


class ToolCallsDecision(BaseModel):
    type: Literal["tool_calls"]
    content: ToolCallsContent


class TextDecision(BaseModel):
    """向用户输出的最终文本（走 sidekick-closuring 校验后 done）。"""

    type: Literal["text"]
    content: str = Field(min_length=1)


class DoneDecision(BaseModel):
    """无文本的纯完成（content 允许字符串说明）。"""

    type: Literal["done"]
    content: Optional[str] = None


LeaderOutput = Annotated[
    Union[ToolCallsDecision, TextDecision, DoneDecision],
    Field(discriminator="type"),
]

_LEADER_ADAPTER = TypeAdapter(LeaderOutput)


def parse_leader_output_dict(data: dict) -> dict[str, Any]:
    """校验已解码的 leader 输出 dict，返回归一化结果。"""
    validated = _LEADER_ADAPTER.validate_python(data)
    return validated.model_dump()


def validate_leader_output(
    parsed: dict[str, Any],
    allowed_tools: set[str],
) -> list[str]:
    """
    语义校验（hard 层，reasoning 内部）：
    - tool_calls 数组非空且 call_seq 唯一；
    - tool_name 必须在允许工具集合内；
    - text/done 的 content 类型正确（pydantic 已保证）。

    Returns:
        问题列表；空表示通过。
    """
    issues: list[str] = []
    if parsed.get("type") != "tool_calls":
        return issues

    content = parsed.get("content") or {}
    calls = content.get("calls") or []
    if not calls:
        issues.append("tool_calls.content.calls 为空数组")

    seqs = [c.get("call_seq") for c in calls]
    if len(seqs) != len(set(seqs)):
        issues.append(f"call_seq 重复: {seqs}")

    for c in calls:
        tool_name = c.get("tool_name")
        if not tool_name:
            issues.append("存在缺少 tool_name 的调用")
            continue
        if allowed_tools and tool_name not in allowed_tools:
            issues.append(f"tool_name '{tool_name}' 不在协议内")

    return issues


def _calls_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "calls": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "call_seq": {"type": "integer", "minimum": 0},
                        "tool_name": {"type": "string"},
                        "tool_args": {"type": "object"},
                        "task_description": {"type": "string"},
                    },
                    "required": ["call_seq", "tool_name"],
                    "additionalProperties": True,
                },
            },
        },
        "required": ["calls"],
        "additionalProperties": True,
    }


def leader_output_json_schema() -> dict:
    """
    返回 OpenAI 标准 structured output 使用的 JSON Schema。

    与一阶段 decision_json_schema 同风格：扁平、strict=False、
    required 仅含 type（保持宽松语义，兼容 pydantic 判别模型 + extra=allow）。
    """
    return {
        "name": "leader_output",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["tool_calls", "text", "done"]},
                "content": {
                    "oneOf": [
                        _calls_schema(),
                        {"type": "string"},
                        {"type": ["string", "null"]},
                    ]
                },
            },
            "required": ["type"],
            "additionalProperties": True,
        },
    }


__all__ = [
    "DoneDecision",
    "LeaderOutput",
    "TextDecision",
    "ToolCallSpec",
    "ToolCallsContent",
    "ToolCallsDecision",
    "leader_output_json_schema",
    "parse_leader_output_dict",
    "validate_leader_output",
]
