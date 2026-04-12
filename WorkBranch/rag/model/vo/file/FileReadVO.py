from __future__ import annotations

from pydantic import BaseModel


class FileReadVO(BaseModel):
    path: str
    name: str
    size: int
    content: str
