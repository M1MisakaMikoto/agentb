from __future__ import annotations

from pydantic import BaseModel


class DocumentUpdateRequestDTO(BaseModel):
    display_name: str
