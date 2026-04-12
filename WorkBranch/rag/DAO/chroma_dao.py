from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.api.models.Collection import Collection


class ChromaDAO:
    """Access vector data through Chroma API only."""

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        self.persist_dir = persist_dir or (base_dir / "chroma_db")
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))

    def get_or_create_collection(self, collection_name: str) -> Collection:
        return self.client.get_or_create_collection(name=collection_name)

    def add_chunk(
        self,
        collection_name: str,
        chunk_id: str,
        chunk: str,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> None:
        collection = self.get_or_create_collection(collection_name)
        payload = {
            "ids": [chunk_id],
            "documents": [chunk],
            "metadatas": [metadata or {}],
        }
        if embedding is not None:
            payload["embeddings"] = [embedding]
        collection.add(**payload)

    def query(
        self,
        collection_name: str,
        query_text: str,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        collection = self.get_or_create_collection(collection_name)
        return collection.query(
            query_texts=[query_text],
            n_results=top_k,
            where=where,
        )

    def add_chunks_batch(
        self,
        collection_name: str,
        chunk_ids: List[str],
        chunks: List[str],
        metadatas: List[Dict[str, Any]],
        embeddings: Optional[List[Optional[List[float]]]] = None,
    ) -> None:
        if not chunk_ids:
            return
        collection = self.get_or_create_collection(collection_name)
        payload: Dict[str, Any] = {
            "ids": chunk_ids,
            "documents": chunks,
            "metadatas": metadatas,
        }
        if embeddings is not None:
            payload["embeddings"] = embeddings
        collection.add(**payload)

    def delete_chunks(self, collection_name: str, chunk_ids: List[str]) -> None:
        if not chunk_ids:
            return
        collection = self.get_or_create_collection(collection_name)
        collection.delete(ids=chunk_ids)
