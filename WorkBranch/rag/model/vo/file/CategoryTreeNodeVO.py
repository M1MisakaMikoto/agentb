from __future__ import annotations

from pydantic import BaseModel


class CategoryTreeNodeVO(BaseModel):
    id: int
    name: str
    parent_id: int | None
    children: list["CategoryTreeNodeVO"]
    created_at: str


CategoryTreeNodeVO.model_rebuild()
