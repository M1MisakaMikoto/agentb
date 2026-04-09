from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class KnowledgeBaseVO(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: str
    updated_at: str
