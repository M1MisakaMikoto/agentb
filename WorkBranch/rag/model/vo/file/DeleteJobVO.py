from __future__ import annotations

from pydantic import BaseModel


class DeleteJobVO(BaseModel):
    id: int
    document_id: int
    storage_key: str
    collection_name: str
    state: str
    attempts: int
    last_error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str
