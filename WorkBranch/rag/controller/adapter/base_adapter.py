from __future__ import annotations

from abc import ABC, abstractmethod


class Adapter(ABC):
    @abstractmethod
    def run(self) -> int:
        raise NotImplementedError

