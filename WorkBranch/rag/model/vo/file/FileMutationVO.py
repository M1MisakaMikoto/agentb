from __future__ import annotations

from pydantic import BaseModel


class FileMutationVO(BaseModel):
    ok: bool
    path: str
    type: str | None = None
