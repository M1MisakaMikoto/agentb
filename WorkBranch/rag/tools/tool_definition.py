from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Type

from pydantic import BaseModel

from rag.tool_schema import BaseToolSchema


class BaseToolDefinition(ABC):
    """Runtime abstraction for executable tools."""

    schema_cls: Type[BaseToolSchema]

    @classmethod
    def tool_name(cls) -> str:
        return cls.schema_cls.tool_name()

    @classmethod
    def tool_description(cls) -> str:
        return cls.schema_cls.tool_description()

    @classmethod
    def input_model(cls) -> Type[BaseModel]:
        return cls.schema_cls.input_model()

    @classmethod
    def output_model(cls) -> Type[BaseModel]:
        return cls.schema_cls.output_model()

    @classmethod
    def tool_definition(cls) -> Dict[str, Any]:
        return cls.schema_cls.tool_definition()

    @classmethod
    def build_tool_list(cls) -> list[Dict[str, Any]]:
        return cls.schema_cls.build_tool_list()

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_model = self.input_model()
        request = request_model(**payload)
        response = self._run(request)
        if isinstance(response, BaseModel):
            return response.model_dump()
        output_model = self.output_model()
        return output_model(**response).model_dump()

    @abstractmethod
    def _run(self, request: BaseModel) -> BaseModel | Dict[str, Any]:
        raise NotImplementedError


# Backward-compatible alias for previous import paths.
Tool_definition = BaseToolDefinition
