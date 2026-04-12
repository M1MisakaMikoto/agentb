from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from rag.tool_schema import RAGChunkHit


@dataclass
class RetrievalCandidate:
    score: float
    recall_index: int
    hit: RAGChunkHit
    metadata: Dict[str, Any]
