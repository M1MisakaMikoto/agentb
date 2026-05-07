from __future__ import annotations

from pydantic import BaseModel


class IdResultVO(BaseModel):
    ok: bool
    id: int
