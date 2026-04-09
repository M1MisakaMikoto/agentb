from __future__ import annotations

from pydantic import BaseModel


class CategoryMutationVO(BaseModel):
    ok: bool
    id: int
