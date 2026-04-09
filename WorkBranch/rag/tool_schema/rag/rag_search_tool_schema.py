from __future__ import annotations

from typing import Any, Dict, Type

from pydantic import BaseModel

from ..base.base_tool_schema import BaseToolSchema
from .rag_search_request import RAGSearchRequest
from .rag_search_response import RAGSearchResponse


class RAGSearchToolSchema(BaseToolSchema):
    """Concrete schema implementation for rag_search tool."""

    @classmethod
    def tool_name(cls) -> str:
        return "rag_search"

    @classmethod
    def tool_description(cls) -> str:
        return "Search the private knowledge base and return cited chunks."

    @classmethod
    def input_model(cls) -> Type[BaseModel]:
        return RAGSearchRequest

    @classmethod
    def output_model(cls) -> Type[BaseModel]:
        return RAGSearchResponse

    @classmethod
    def tool_definition(cls) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": cls.tool_name(),
                "description": cls.tool_description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "User question to search in the KB.",
                            "minLength": 1,
                            "maxLength": 512,
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of chunks to return after ranking.",
                            "minimum": 1,
                            "maximum": 30,
                            "default": 8,
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["dense", "hybrid"],
                            "default": "hybrid",
                        },
                        "min_score": {
                            "type": "number",
                            "description": "Filter chunks by normalized score in [0, 1].",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "use_rerank": {
                            "type": "boolean",
                            "default": True,
                        },
                        "rewrite_query": {
                            "type": "boolean",
                            "default": False,
                        },
                        "filters": {
                            "type": "object",
                            "properties": {
                                "collection_ids": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                                "doc_ids": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                                "source_types": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "metadata": {
                                    "type": "object",
                                    "additionalProperties": True,
                                },
                            },
                            "additionalProperties": False,
                        },
                        "kb_id": {
                            "type": "integer",
                            "description": "Knowledge base ID to search in. Omit to search the default collection.",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }

