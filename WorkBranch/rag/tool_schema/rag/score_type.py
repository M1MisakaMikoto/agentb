from __future__ import annotations

from enum import Enum


class ScoreType(str, Enum):
    distance = "distance"
    similarity = "similarity"
    rerank = "rerank"

