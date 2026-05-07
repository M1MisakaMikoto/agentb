from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class KnowledgeBaseDO:
    id: int
    name: str
    description: Optional[str]
    created_at: str
    updated_at: str
