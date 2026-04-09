from __future__ import annotations

from pydantic import BaseModel


class DocumentListItemVO(BaseModel):
    id: int
    display_name: str
    filename: str
    storage_key: str
    mime_type: str
    size_bytes: int
    status: str
    updated_at: str
    created_at: str
