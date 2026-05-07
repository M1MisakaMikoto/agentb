from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional


class BaseEmbeddingEngine(ABC):
    name: str = "base"

    @abstractmethod
    def embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        Return embedding vectors for texts, or None to delegate embedding to vector store.
        """
        raise NotImplementedError

