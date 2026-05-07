from __future__ import annotations

from pydantic import BaseModel


class DeleteActionResultVO(BaseModel):
    ok: bool
    job_id: int
    document_id: int
    state: str
    error: str | None = None
    note: str | None = None
