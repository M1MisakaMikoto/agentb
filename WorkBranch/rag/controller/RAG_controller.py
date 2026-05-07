from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Allow direct execution: `python backend/controller/RAG_controller.py`
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.tool_schema import RAGSearchRequest, RAGSearchResponse
from rag.tools.RAG_tool import RAGTool
from rag.controller.adapter import CLIAdapter, MCPAdapter


class RAGController:
    """Application controller that dispatches request to tool runtime."""

    def __init__(self, tool: Optional[RAGTool] = None) -> None:
        self.tool = tool or RAGTool()

    def handle(self, payload: Dict[str, Any]) -> RAGSearchResponse:
        request = RAGSearchRequest(**payload)
        response_dict = self.tool.run(request.model_dump())
        return RAGSearchResponse(**response_dict)

    def close(self) -> None:
        self.tool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RAG controller with CLI and MCP adapters")
    parser.add_argument(
        "--adapter",
        choices=["cli", "mcp"],
        default="cli",
        help="Adapter mode: cli or mcp",
    )
    known_args, remaining = parser.parse_known_args(argv)

    controller = RAGController()
    try:
        if known_args.adapter == "mcp":
            return MCPAdapter(controller).run()
        return CLIAdapter(controller, argv=remaining).run()
    finally:
        controller.close()


if __name__ == "__main__":
    sys.exit(main())
