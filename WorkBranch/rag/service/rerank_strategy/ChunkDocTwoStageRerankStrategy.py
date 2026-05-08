from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

from .BaseRerankStrategy import BaseRerankStrategy
from .RetrievalCandidate import RetrievalCandidate


@dataclass
class _DocBucket:
    key: str
    doc_score: float
    first_recall_index: int
    candidates: List[RetrievalCandidate]


class ChunkDocTwoStageRerankStrategy(BaseRerankStrategy):
    """Two-stage rerank:
    1) chunk -> doc aggregation score
    2) rank docs first, then emit best chunk per doc in rounds
    """

    order = 300
    enabled = True
    SECONDARY_WEIGHT = 0.2

    @property
    def name(self) -> str:
        return "chunk_doc_two_stage_v1"

    @staticmethod
    def _doc_key(candidate: RetrievalCandidate) -> str:
        if candidate.hit.doc_id > 0:
            return f"doc:{candidate.hit.doc_id}"
        return f"chunk:{candidate.hit.chunk_id}"

    @classmethod
    def _build_doc_bucket(cls, key: str, candidates: List[RetrievalCandidate]) -> _DocBucket:
        sorted_candidates = sorted(candidates, key=lambda row: (-row.score, row.recall_index))
        max_score = sorted_candidates[0].score
        second_score = sorted_candidates[1].score if len(sorted_candidates) > 1 else 0.0
        doc_score = round(max_score + cls.SECONDARY_WEIGHT * second_score, 6)
        first_recall_index = min(row.recall_index for row in sorted_candidates)
        return _DocBucket(
            key=key,
            doc_score=doc_score,
            first_recall_index=first_recall_index,
            candidates=sorted_candidates,
        )

    def rank(self, candidates: List[RetrievalCandidate], top_k: int) -> List[RetrievalCandidate]:
        if top_k <= 0 or not candidates:
            return []

        grouped: Dict[str, List[RetrievalCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[self._doc_key(candidate)].append(candidate)

        buckets: List[_DocBucket] = []
        for key, rows in grouped.items():
            buckets.append(self._build_doc_bucket(key, rows))

        buckets.sort(key=lambda bucket: (-bucket.doc_score, bucket.first_recall_index))

        ranked: List[RetrievalCandidate] = []
        round_index = 0
        while len(ranked) < top_k:
            emitted = False
            for bucket in buckets:
                if round_index < len(bucket.candidates):
                    ranked.append(bucket.candidates[round_index])
                    emitted = True
                    if len(ranked) >= top_k:
                        break
            if not emitted:
                break
            round_index += 1

        return ranked
