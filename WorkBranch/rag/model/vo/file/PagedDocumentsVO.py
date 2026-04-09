from __future__ import annotations

from pydantic import BaseModel

from .DocumentListItemVO import DocumentListItemVO


class PagedDocumentsVO(BaseModel):
    page: int
    size: int
    total: int
    items: list[DocumentListItemVO]
