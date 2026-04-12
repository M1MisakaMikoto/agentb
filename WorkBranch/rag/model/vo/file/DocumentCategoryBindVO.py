from __future__ import annotations

from pydantic import BaseModel


class DocumentCategoryBindVO(BaseModel):
    ok: bool
    document_id: int
    category_id: int
