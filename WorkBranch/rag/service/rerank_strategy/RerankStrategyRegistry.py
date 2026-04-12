from __future__ import annotations

from typing import Dict

from .BaseRerankStrategy import BaseRerankStrategy


class RerankStrategyRegistry:
    def __init__(self) -> None:
        self._strategies: Dict[str, BaseRerankStrategy] = {}

    def register(self, strategy: BaseRerankStrategy) -> "RerankStrategyRegistry":
        self._strategies[strategy.name] = strategy
        return self

    def get(self, name: str) -> BaseRerankStrategy:
        strategy = self._strategies.get(name)
        if strategy is None:
            raise ValueError(f"rerank strategy not found: {name}")
        return strategy
