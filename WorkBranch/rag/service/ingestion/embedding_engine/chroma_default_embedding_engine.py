from __future__ import annotations

from typing import List, Optional

from .base_embedding_engine import BaseEmbeddingEngine


class ChromaDefaultEmbeddingEngine(BaseEmbeddingEngine):
    """
    Delegate embedding computation to Chroma collection default embedding function.
    """

    name = "chroma_default"

    def embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        return None

