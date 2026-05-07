from __future__ import annotations

from pydantic import BaseModel, Field


class UpdateFileRequestDTO(BaseModel):
    path: str = Field(..., description="Relative path under DOCS")
    content: str = ""
