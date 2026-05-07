from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RAGSearchError(BaseModel):
    code: Literal[
        "INVALID_INPUT",
        "PERMISSION_DENIED",
        "COLLECTION_NOT_FOUND",
        "VECTORSTORE_ERROR",
        "INTERNAL_ERROR",
    ]
    message: str

