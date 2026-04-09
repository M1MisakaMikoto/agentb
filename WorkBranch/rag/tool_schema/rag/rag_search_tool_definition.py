from __future__ import annotations

from typing import Any, Dict, List

from .rag_search_tool_schema import RAGSearchToolSchema

RAG_SEARCH_TOOL_DEFINITION: Dict[str, Any] = RAGSearchToolSchema.tool_definition()


def build_tool_list() -> List[Dict[str, Any]]:
    """Helper for OpenAI Responses/Chat Completions tool registration."""
    return RAGSearchToolSchema.build_tool_list()
