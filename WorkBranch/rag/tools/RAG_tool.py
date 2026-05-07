from __future__ import annotations

from typing import Optional

from rag.service.RAG_service import RAG_service
from rag.tool_schema import RAGSearchRequest, RAGSearchResponse, RAGSearchToolSchema
from rag.tools.tool_definition import BaseToolDefinition


class RAGTool(BaseToolDefinition):
    schema_cls = RAGSearchToolSchema

    def __init__(self, service: Optional[RAG_service] = None) -> None:
        self.service = service or RAG_service()

    def _run(self, request: RAGSearchRequest) -> RAGSearchResponse:
        return self.service.rag_search(request)

    def close(self) -> None:
        self.service.close()


# Backward-compatible alias for previous class name.
RAG_tool = RAGTool

