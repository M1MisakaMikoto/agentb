from __future__ import annotations

from typing import Any

from rag.tool_schema import RAGSearchToolSchema

from .base_adapter import Adapter

try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - optional runtime dependency
    FastMCP = None


class MCPAdapter(Adapter):
    def __init__(self, controller: Any, server_name: str = "rag-controller") -> None:
        self.controller = controller
        self.server_name = server_name

    def run(self) -> int:
        if FastMCP is None:
            raise RuntimeError("MCP adapter requires dependency: pip install 'mcp[cli]'")

        mcp = FastMCP(self.server_name)

        @mcp.tool(
            name=RAGSearchToolSchema.tool_name(),
            description=RAGSearchToolSchema.tool_description(),
        )
        def rag_search(**kwargs: Any) -> dict[str, Any]:
            response = self.controller.handle(kwargs)
            return response.model_dump()

        mcp.run()
        return 0

