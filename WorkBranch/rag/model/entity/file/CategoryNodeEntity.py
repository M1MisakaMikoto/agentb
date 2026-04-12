from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CategoryNodeEntity:
    id: int
    name: str
    parent_id: Optional[int]
    created_at: str
    children: List["CategoryNodeEntity"] = field(default_factory=list)
