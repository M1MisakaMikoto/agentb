from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from rag.DAO.RAG_DAO import RAG_DAO
from rag.DAO.ingestion_meta_dao import IngestionMetaDAO
from rag.DAO.knowledge_base_dao import KnowledgeBaseDAO
from rag.logging_utils import get_logger
from rag.service.ingestion.chunk_engine.registry import ChunkEngineRegistry
from rag.service.ingestion.embedding_engine.base_embedding_engine import BaseEmbeddingEngine
from rag.service.ingestion.embedding_engine.bge_embedding_engine import BgeEmbeddingEngine

LOGGER = get_logger(__name__)


class IngestionService:
    """
    Ingestion pipeline:
    1) select chunk engine (pypdf / ocr / text)
    2) chunk file content
    3) optional embedding via embedding engine
    4) write chunks into RAG DAO
    """

    def __init__(
        self,
        rag_dao: Optional[RAG_DAO] = None,
        meta_dao: Optional[IngestionMetaDAO] = None,
        chunk_registry: Optional[ChunkEngineRegistry] = None,
        embedding_engine: Optional[BaseEmbeddingEngine] = None,
        meta_db: Optional[Path] = None,
        docs_root: Optional[Path] = None,
    ) -> None:
        root = Path(__file__).resolve().parents[3]
        self.meta_db = meta_db or (root / "rag" / "file_meta.sqlite3")
        self.docs_root = docs_root or (root / "DOCS")
        self.rag_dao = rag_dao or RAG_DAO()
        self.meta_dao = meta_dao or IngestionMetaDAO(db_path=self.meta_db)
        self.chunk_registry = chunk_registry or ChunkEngineRegistry()
        self.embedding_engine = embedding_engine or BgeEmbeddingEngine()

    def _storage_abs(self, storage_key: str) -> Path:
        key = storage_key.replace("\\", "/").lstrip("/")
        if key.startswith("raw/"):
            resolved = (self.docs_root / key).resolve()
        else:
            resolved = (self.docs_root / "raw" / key).resolve()
        if not resolved.is_relative_to(self.docs_root.resolve()):
            raise ValueError(f"storage_key escapes docs_root: {storage_key!r}")
        return resolved

    def ingest_document(self, document_id: int, collection_name: str = "default") -> Dict[str, Any]:
        job_id = self.meta_dao.create_job(document_id)
        self.meta_dao.set_document_status(document_id, "indexing")
        self.meta_dao.set_job_running(job_id)

        try:
            ctx = self.meta_dao.get_doc_context(document_id)
            # 知识库隔离：根据文档所属 kb_id 计算实际 collection_name
            kb_id: Optional[int] = ctx.get("kb_id")
            collection_name = KnowledgeBaseDAO.get_collection_name(kb_id)
            LOGGER.info("ingest_started document_id=%s job_id=%s collection=%s kb_id=%s", document_id, job_id, collection_name, kb_id)
            source_path = self._storage_abs(ctx["storage_key"])
            if not source_path.exists():
                raise FileNotFoundError(f"source file not found: {source_path}")

            chunk_engine, profile = self.chunk_registry.select(source_path, ctx["mime_type"])
            chunks = chunk_engine.chunk(source_path)
            if not chunks:
                raise ValueError("no chunks extracted from source")
            LOGGER.info(
                "ingest_chunked document_id=%s job_id=%s engine=%s mime=%s text_ratio=%.4f chunk_count=%s source=%s",
                document_id,
                job_id,
                chunk_engine.name,
                profile.mime,
                profile.text_ratio,
                len(chunks),
                str(source_path),
            )

            vectors = self.embedding_engine.embed_texts(chunks)
            payloads: List[Dict[str, Any]] = []
            for idx, text in enumerate(chunks):
                metadata = {
                    "document_id": str(document_id),
                    "doc_title": ctx["display_name"],
                    "source": ctx["storage_key"],
                    "source_type": source_path.suffix.replace(".", "").lower(),
                    "category_ids": ",".join(str(c) for c in ctx["category_ids"]),
                    "primary_category_id": str(ctx["primary_category_id"] or ""),
                    "chunk_engine": chunk_engine.name,
                    "chunk_profile_mime": profile.mime,
                    "chunk_profile_text_ratio": str(profile.text_ratio),
                    "embedding_engine": self.embedding_engine.name,
                }
                row = {
                    "text": text,
                    "chunk_index": idx,
                    "metadata": metadata,
                    "source": ctx["storage_key"],
                }
                if vectors is not None:
                    row["embedding"] = vectors[idx]
                payloads.append(row)

            # Re-index after successful preprocessing to reduce "no-index" window on failures.
            self.rag_dao.delete_doc(document_id=str(document_id), collection_name=collection_name)

            chunk_ids = self.rag_dao.add_chunks(
                chunks=payloads,
                collection_name=collection_name,
                document_id=str(document_id),
                source=ctx["storage_key"],
            )

            self.meta_dao.set_document_status(document_id, "ready")
            self.meta_dao.set_job_finished(job_id, "success")
            LOGGER.info(
                "ingest_succeeded document_id=%s job_id=%s collection=%s chunk_count=%s embedding_engine=%s",
                document_id,
                job_id,
                collection_name,
                len(chunk_ids),
                self.embedding_engine.name,
            )
            return {"ok": True, "job_id": job_id, "chunk_count": len(chunk_ids)}
        except Exception as exc:
            self.meta_dao.set_document_status(document_id, "failed")
            self.meta_dao.set_job_finished(job_id, "failed", error_message=str(exc))
            LOGGER.exception(
                "ingest_failed document_id=%s job_id=%s collection=%s error=%s",
                document_id,
                job_id,
                collection_name,
                str(exc),
            )
            return {"ok": False, "job_id": job_id, "error": str(exc)}

    def close(self) -> None:
        self.rag_dao.close()