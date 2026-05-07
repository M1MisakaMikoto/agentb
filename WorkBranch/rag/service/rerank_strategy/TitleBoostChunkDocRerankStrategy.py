from __future__ import annotations

from typing import List

from .BaseRerankStrategy import BaseRerankStrategy
from .RetrievalCandidate import RetrievalCandidate
from .TitleBoostRerankStrategy import TitleBoostRerankStrategy
from .ChunkDocTwoStageRerankStrategy import ChunkDocTwoStageRerankStrategy


class TitleBoostChunkDocRerankStrategy(BaseRerankStrategy):
    def __init__(
        self,
        boost_keywords: set[str] | None = None,
        boost_weight: float = 0.3,
        min_match_tokens: int = 1,
    ) -> None:
        self._boost = TitleBoostRerankStrategy(
            boost_keywords=boost_keywords,
            boost_weight=boost_weight,
            min_match_tokens=min_match_tokens,
        )
        self._rerank = ChunkDocTwoStageRerankStrategy()

    @property
    def name(self) -> str:
        return "title_boost_chunk_doc_v1"

    def rank(self, candidates: List[RetrievalCandidate], top_k: int) -> List[RetrievalCandidate]:
        if top_k <= 0 or not candidates:
            return []
        boosted = self._boost.rank(candidates, top_k=len(candidates))
        return self._rerank.rank(boosted, top_k=top_k)
