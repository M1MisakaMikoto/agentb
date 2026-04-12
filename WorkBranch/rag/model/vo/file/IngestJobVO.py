from __future__ import annotations

from pydantic import BaseModel


class IngestJobVO(BaseModel):
    id: int
    document_id: int
    status: str
    error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
