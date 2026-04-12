from __future__ import annotations

from pydantic import BaseModel


class DeleteCategoryResultVO(BaseModel):
    ok: bool
    id: int | None = None
    mode: str | None = None
    deleted_categories: list[int] | None = None
