from __future__ import annotations

from pydantic import BaseModel, Field


class CitationSpan(BaseModel):
    start: int = Field(..., ge=0)
    end: int = Field(..., gt=0)

