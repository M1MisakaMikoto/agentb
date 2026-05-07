from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .rag_search_filters import RAGSearchFilters
from .retrieval_mode import RetrievalMode


class RAGSearchRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Natural-language query for retrieval.",
    )
    top_k: int = Field(
        default=8,
        ge=1,
        le=30,
        description="How many chunks to return after final ranking.",
    )
    mode: RetrievalMode = Field(
        default=RetrievalMode.hybrid,
        description="Retrieval mode.",
    )
    min_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Discard chunks below this normalized score.",
    )
    use_rerank: bool = Field(
        default=True,
        description="Whether to rerank recalled chunks.",
    )
    rewrite_query: bool = Field(
        default=False,
        description="Whether to rewrite the query before retrieval.",
    )
    filters: Optional[RAGSearchFilters] = Field(
        default=None,
        description="Permission and business filters.",
    )
    kb_id: Optional[int] = Field(
        default=None,
        description="Knowledge base ID to search in. If None, uses the default collection.",
    )

