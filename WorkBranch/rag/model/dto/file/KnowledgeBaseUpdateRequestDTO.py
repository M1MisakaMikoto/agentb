from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class KnowledgeBaseUpdateRequestDTO(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100, description="新名称，不传则不修改")
    description: Optional[str] = Field(default=None, max_length=500, description="新描述，不传则不修改")
