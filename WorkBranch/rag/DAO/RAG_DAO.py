from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .base_dao import BaseRAGDAO
from .sqlite_vec_dao import SqliteVecDAO

LOGGER = logging.getLogger(__name__)

class RAG_DAO(BaseRAGDAO):
    """
    Facade DAO:
    Now delegates completely to SqliteVecDAO for unified transaction storage of metadata and vectors.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            # Match existing backend db relative path
            app_root = Path(__file__).resolve().parents[2]
            db_path = app_root / "rag" / "file_meta.sqlite3"
        
        self.vec_dao = SqliteVecDAO(db_path)
        LOGGER.info("RAG_DAO initialized with SqliteVecDAO")

    def _parse_kb_id(self, collection_name: str) -> Optional[int]:
        if collection_name and collection_name.startswith("kb_"):
            try:
                return int(collection_name[3:])
            except ValueError:
                pass
        return None

    def add_chunks(
        self,
        chunks: List[Dict[str, Any]],
        collection_name: str = "default",
        document_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[str]:
        kb_id = self._parse_kb_id(collection_name)
        return self.vec_dao.add_chunks_batch(
            chunks=chunks,
            kb_id=kb_id,
            document_id=document_id,
            source=source
        )

    def search(
        self,
        query_vector: List[float],
        collection_name: str = "default",
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
        document_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        kb_id = self._parse_kb_id(collection_name)
        return self.vec_dao.search(
            query_vector=query_vector,
            kb_id=kb_id,
            top_k=top_k,
            where_filter=where,
            document_id=document_id
        )

    def delete_doc(self, document_id: str, collection_name: str = "default") -> int:
        return self.vec_dao.delete_doc(document_id=document_id)

    def get_doc_chunks(self, document_id: str, collection_name: str = "default") -> List[Dict[str, Any]]:
        return self.vec_dao.get_doc_chunks(document_id=document_id)

    def close(self) -> None:
        self.vec_dao.close()