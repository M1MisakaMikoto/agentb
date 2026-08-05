from __future__ import annotations

from typing import List, Optional

from .base_embedding_engine import BaseEmbeddingEngine


class DummyEmbeddingEngine(BaseEmbeddingEngine):
    """Temporary no-op embedding engine used when external embedding is disabled.

    Returns deterministic all-zero vectors (1024 dims, matching BGE-M3) so the
    ingestion/search pipeline runs without an embedding service. Search results
    are not semantically ranked; enable a real embedding engine before relying
    on retrieval quality.
    """

    name = "dummy_zero"

    def __init__(self, dimension: int = 1024, max_workers: int = 1) -> None:
        self.dimension = max(1, int(dimension))
        self.max_workers = max(1, int(max_workers))

    def embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not texts:
            return []
        zero: List[float] = [0.0] * self.dimension
        return [list(zero) for _ in texts]
