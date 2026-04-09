from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DeleteCategoryResultDO:
    ok: bool
    id: Optional[int] = None
    mode: Optional[str] = None
    deleted_categories: Optional[List[int]] = None
