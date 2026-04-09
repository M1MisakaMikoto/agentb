from __future__ import annotations

from pydantic import BaseModel


class FileListEntryVO(BaseModel):
    name: str
    path: str
    type: str
    size: int | None = None
