from __future__ import annotations

from enum import Enum


class RetrievalMode(str, Enum):
    dense = "dense"
    hybrid = "hybrid"

