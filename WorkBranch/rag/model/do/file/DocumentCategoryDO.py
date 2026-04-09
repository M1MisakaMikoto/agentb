from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DocumentCategoryDO:
    id: int
    name: str
    is_primary: bool
