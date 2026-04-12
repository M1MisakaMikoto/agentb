from __future__ import annotations

from typing import List

from .BaseRerankStrategy import BaseRerankStrategy
from .RetrievalCandidate import RetrievalCandidate


class ChunkScoreRerankStrategy(BaseRerankStrategy):
    @property
    def name(self) -> str:
        return "chunk_score_v1"

    def rank(self, candidates: List[RetrievalCandidate], top_k: int) -> List[RetrievalCandidate]:
        ranked = sorted(candidates, key=lambda row: (-row.score, row.recall_index))
        return ranked[:top_k]
