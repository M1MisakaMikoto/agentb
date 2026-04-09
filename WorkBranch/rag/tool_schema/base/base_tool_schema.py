from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Type

from pydantic import BaseModel


class BaseToolSchema(ABC):
    """Base contract for all tool schema definitions."""

    @classmethod
    @abstractmethod
    def tool_name(cls) -> str:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def tool_description(cls) -> str:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def input_model(cls) -> Type[BaseModel]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def output_model(cls) -> Type[BaseModel]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def tool_definition(cls) -> Dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def build_tool_list(cls) -> List[Dict[str, Any]]:
        return [cls.tool_definition()]

