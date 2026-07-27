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


def parse_decision_response(response_text: str) -> dict[str, Any]:
    """Parse one strict top-level decision object while preserving extra fields."""
    decision = _DECISION_RESPONSE_ADAPTER.validate_json(response_text)
    return decision.model_dump()


def format_decision_validation_error(error: ValidationError) -> str:
    """Return every validation issue without repeating or truncating the input."""
    return error.json(include_url=False, include_input=False)
