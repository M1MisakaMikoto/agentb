from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .RetrievalCandidate import RetrievalCandidate


class BaseRerankStrategy(ABC):
    order = 100
    enabled = True

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    def is_enabled(self) -> bool:
        return bool(self.enabled)

    @abstractmethod
    def rank(self, candidates: List[RetrievalCandidate], top_k: int) -> List[RetrievalCandidate]:
        raise NotImplementedError
