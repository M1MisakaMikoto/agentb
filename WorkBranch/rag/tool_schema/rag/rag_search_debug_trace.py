from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RAGSearchDebugTrace(BaseModel):
    rewritten_query: Optional[str] = None
    recall_k: int = Field(default=0, ge=0)
    rerank_model: Optional[str] = None
    latency_ms: int = Field(default=0, ge=0)

