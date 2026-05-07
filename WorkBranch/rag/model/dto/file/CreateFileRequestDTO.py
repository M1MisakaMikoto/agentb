from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateFileRequestDTO(BaseModel):
    path: str = Field(..., description="Relative path under DOCS")
    type: Literal["file", "dir"] = "file"
    content: str = ""
    overwrite: bool = False
