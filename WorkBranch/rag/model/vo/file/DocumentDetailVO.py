from __future__ import annotations

from pydantic import BaseModel

from .DocumentCategoryVO import DocumentCategoryVO
from .DocumentListItemVO import DocumentListItemVO


class DocumentDetailVO(BaseModel):
    document: DocumentListItemVO
    categories: list[DocumentCategoryVO]
