from __future__ import annotations

from pydantic import BaseModel

from .CategoryTreeNodeVO import CategoryTreeNodeVO


class CategoryTreeResponseVO(BaseModel):
    items: list[CategoryTreeNodeVO]
