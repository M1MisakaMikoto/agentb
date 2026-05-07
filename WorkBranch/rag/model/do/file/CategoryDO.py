from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CategoryDO:
    id: int
    name: str
    parent_id: Optional[int]
    created_at: str
