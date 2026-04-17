from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseRAGDAO(ABC):
    """Standard DAO contract for RAG persistence and retrieval."""
    @abstractmethod
    def add_chunk(
        self,
        chunk: str,
        chunk_id: Optional[str] = None,
        collection_name: str = "default",
        document_id: Optional[str] = None,
        chunk_index: Optional[int] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> str: ...

    @abstractmethod
    def add_chunks(
        self,
        chunks: List[Dict[str, Any]],
        collection_name: str = "default",
        document_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[str]: ...

    @abstractmethod
    def search(
        self,
        query_vector: List[float],
        collection_name: str = "default",
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
        document_id: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    def delete_doc(self, document_id: str, collection_name: str = "default") -> int: ...

    @abstractmethod
    def get_doc_chunks(self, document_id: str, collection_name: str = "default") -> List[Dict[str, Any]]: ...

    @abstractmethod
    def close(self) -> None: ...
