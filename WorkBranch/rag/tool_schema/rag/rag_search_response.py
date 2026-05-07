from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from .rag_chunk_hit import RAGChunkHit
from .rag_search_debug_trace import RAGSearchDebugTrace
from .rag_search_error import RAGSearchError


class RAGSearchResponse(BaseModel):
    ok: bool = True
    trace_id: str
    query: str
    items: List[RAGChunkHit] = Field(default_factory=list)
    debug: Optional[RAGSearchDebugTrace] = None
    error: Optional[RAGSearchError] = None

    @model_validator(mode="after")
    def validate_ok_and_error(self) -> "RAGSearchResponse":
        if self.ok and self.error is not None:
            raise ValueError("error must be None when ok=True")
        if not self.ok and self.error is None:
            raise ValueError("error is required when ok=False")
        return self

