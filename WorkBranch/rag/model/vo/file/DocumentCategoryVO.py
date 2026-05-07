from __future__ import annotations

from pydantic import BaseModel


class DocumentCategoryVO(BaseModel):
    id: int
    name: str
    is_primary: bool
