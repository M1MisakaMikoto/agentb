from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CategoryUpdateRequestDTO(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
