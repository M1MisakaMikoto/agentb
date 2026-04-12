from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DocumentUploadVO(BaseModel):
    ok: bool
    id: int
    storage_key: str
    ingest: dict[str, Any]
