from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .RetrievalCandidate import RetrievalCandidate


class BaseRerankStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def rank(self, candidates: List[RetrievalCandidate], top_k: int) -> List[RetrievalCandidate]:
        raise NotImplementedError
