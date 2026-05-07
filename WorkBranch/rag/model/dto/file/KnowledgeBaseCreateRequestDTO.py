from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class KnowledgeBaseCreateRequestDTO(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="知识库名称，同名不可重复")
    description: Optional[str] = Field(default=None, max_length=500, description="知识库描述")
