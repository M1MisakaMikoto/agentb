from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RAGSearchFilters(BaseModel):
    collection_ids: Optional[List[int]] = Field(
        default=None,
        description="Limit retrieval to specified collection ids.",
    )
    doc_ids: Optional[List[int]] = Field(
        default=None,
        description="Limit retrieval to specified document ids.",
    )
    source_types: Optional[List[str]] = Field(
        default=None,
        description="Limit retrieval to source file types, e.g. ['pdf', 'docx'].",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Exact-match metadata filters, e.g. {'department': 'road'}.",
    )

