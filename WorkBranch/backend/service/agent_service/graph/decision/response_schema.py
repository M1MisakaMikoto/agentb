from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class _DecisionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class ToolDecision(_DecisionResponse):
    kind: Literal["tool"]


class StepDoneDecision(_DecisionResponse):
    kind: Literal["step_done"]


class BlockedDecision(_DecisionResponse):
    kind: Literal["blocked"]


DecisionResponse = Annotated[
    Union[ToolDecision, StepDoneDecision, BlockedDecision],
    Field(discriminator="kind"),
]

_DECISION_RESPONSE_ADAPTER = TypeAdapter(DecisionResponse)

_DECISION_JSON_SCHEMA: dict = {
    "name": "tool_decision",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["tool", "step_done", "blocked"]},
            "tool_name": {"type": "string"},
            "tool_args": {"type": "object"},
            "task_description": {"type": "string"},
            "reason": {"type": "string"},
            "reply": {"type": "string"},
        },
        "required": ["kind"],
        "additionalProperties": True,
    },
}


def decision_json_schema() -> dict:
    """
    返回 OpenAI 标准 structured output 使用的 JSON Schema。

    采用扁平 schema（不用 $defs/oneOf），兼容大多数 OpenAI 兼容端点的
    response_format={"type": "json_schema"}；required 仅含 kind，
    与 pydantic 判别模型 + extra="allow" 的宽松语义一致。
    """
    return _DECISION_JSON_SCHEMA


def parse_decision_response(response_text: str) -> dict[str, Any]:
    """Parse one strict top-level decision object while preserving extra fields."""
    decision = _DECISION_RESPONSE_ADAPTER.validate_json(response_text)
    return decision.model_dump()


def parse_decision_dict(decision: dict) -> dict[str, Any]:
    """Validate an already-decoded decision dict (used by the tolerant parser)."""
    validated = _DECISION_RESPONSE_ADAPTER.validate_python(decision)
    return validated.model_dump()


def format_decision_validation_error(error: ValidationError) -> str:
    """Return every validation issue without repeating or truncating the input."""
    return error.json(include_url=False, include_input=False)
