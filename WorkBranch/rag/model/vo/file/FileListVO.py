from __future__ import annotations

from pydantic import BaseModel

from .FileListEntryVO import FileListEntryVO


class FileListVO(BaseModel):
    root: str
    cwd: str
    parent: str | None = None
    entries: list[FileListEntryVO]
