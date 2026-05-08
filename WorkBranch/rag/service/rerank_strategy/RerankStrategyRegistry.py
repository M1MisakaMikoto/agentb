from __future__ import annotations

from typing import Dict, List

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

    def get_enabled_ordered(self) -> List[BaseRerankStrategy]:
        enabled = [strategy for strategy in self._strategies.values() if strategy.is_enabled()]
        return sorted(enabled, key=lambda strategy: (strategy.order, strategy.name))
