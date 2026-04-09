from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CategoryCreateRequestDTO(BaseModel):
    name: str
    parent_id: Optional[int] = None
