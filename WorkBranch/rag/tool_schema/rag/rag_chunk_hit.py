from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from .citation_span import CitationSpan
from .score_type import ScoreType


class RAGChunkHit(BaseModel):
    chunk_id: str
    doc_id: int
    doc_title: str
    source: str
    source_type: str
    text: str
    score: float
    score_type: ScoreType = ScoreType.similarity
    rank: int = Field(..., ge=1)
    token_count: Optional[int] = Field(default=None, ge=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    citation: Optional[CitationSpan] = None

