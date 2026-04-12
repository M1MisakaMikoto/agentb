from __future__ import annotations

import argparse
from typing import Any, Optional

from .base_adapter import Adapter


class CLIAdapter(Adapter):
    def __init__(self, controller: Any, argv: Optional[list[str]] = None) -> None:
        self.controller = controller
        self.argv = argv

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="RAG search CLI adapter")
        parser.add_argument("--query", required=True, help="User query")
        parser.add_argument("--top-k", type=int, default=8, help="Top K chunks (1-30)")
        parser.add_argument("--min-score", type=float, default=None, help="Score threshold in [0,1]")
        parser.add_argument("--rewrite-query", action="store_true", help="Enable query rewrite")
        parser.add_argument("--no-rerank", action="store_true", help="Disable rerank")
        parser.add_argument(
            "--doc-ids",
            default="",
            help="Comma-separated doc ids, e.g. 1,2,3",
        )
        parser.add_argument(
            "--source-types",
            default="",
            help="Comma-separated source types, e.g. pdf,docx",
        )
        parser.add_argument(
            "--json-only",
            action="store_true",
            help="Print response JSON only",
        )
        return parser

    @staticmethod
    def _parse_csv_int(value: str) -> list[int]:
        if not value.strip():
            return []
        return [int(part.strip()) for part in value.split(",") if part.strip()]

    @staticmethod
    def _parse_csv_str(value: str) -> list[str]:
        if not value.strip():
            return []
        return [part.strip() for part in value.split(",") if part.strip()]

    def run(self) -> int:
        parser = self._build_parser()
        args = parser.parse_args(self.argv)

        filters: dict[str, Any] = {}
        doc_ids = self._parse_csv_int(args.doc_ids)
        source_types = self._parse_csv_str(args.source_types)
        if doc_ids:
            filters["doc_ids"] = doc_ids
        if source_types:
            filters["source_types"] = source_types

        payload: dict[str, Any] = {
            "query": args.query,
            "top_k": args.top_k,
            "rewrite_query": args.rewrite_query,
            "use_rerank": not args.no_rerank,
        }
        if args.min_score is not None:
            payload["min_score"] = args.min_score
        if filters:
            payload["filters"] = filters

        response = self.controller.handle(payload)
        if args.json_only:
            print(response.model_dump_json(ensure_ascii=False, indent=2))
            return 0 if response.ok else 1

        if not response.ok:
            print(f"RAG error: {response.error.code} - {response.error.message}")
            return 1

        print(f"trace_id: {response.trace_id}")
        print(f"items: {len(response.items)}")
        for item in response.items:
            print(f"- [{item.rank}] {item.source} score={item.score:.4f}")
            print(f"  {item.text[:160].replace(chr(10), ' ')}")
        return 0

