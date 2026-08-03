from __future__ import annotations

from pathlib import Path
import os
import re
from typing import Any, Dict, List, Optional

from rag.DAO.RAG_DAO import RAG_DAO
from rag.DAO.ingestion_meta_dao import IngestionMetaDAO
from rag.DAO.knowledge_base_dao import KnowledgeBaseDAO
from rag.logging_utils import get_logger
from rag.service.ingestion.chunk_engine.registry import ChunkEngineRegistry
from rag.service.ingestion.embedding_engine.base_embedding_engine import BaseEmbeddingEngine
from rag.service.ingestion.embedding_engine.OllamaEmbeddingEngine import OllamaEmbeddingEngine

LOGGER = get_logger(__name__)


class IngestionService:
    """
    Ingestion pipeline:
    1) select chunk engine (pypdf / ocr / text)
    2) chunk file content
    3) optional embedding via embedding engine
    4) write chunks into RAG DAO
    """

    PAGE_PATTERN = re.compile(r"^\[page:(\d+)\]\s*", re.IGNORECASE)

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
        self.meta_db = meta_db or Path(
            os.getenv("AGENTB_RAG_META_DB", str(root / "rag" / "file_meta.sqlite3"))
        )
        self.docs_root = docs_root or Path(
            os.getenv("AGENTB_RAG_DOCS_ROOT", str(root / "DOCS"))
        )
        self.rag_dao = rag_dao or RAG_DAO(db_path=self.meta_db)
        self.meta_dao = meta_dao or IngestionMetaDAO(db_path=self.meta_db)
        self.chunk_registry = chunk_registry or ChunkEngineRegistry()
        self.embedding_engine = embedding_engine or OllamaEmbeddingEngine(
            base_url="http://127.0.0.1:11434",
            model="bge-m3:latest",
        )

    def _storage_abs(self, storage_key: str) -> Path:
        key = storage_key.replace("\\", "/").lstrip("/")
        if key.startswith("raw/"):
            resolved = (self.docs_root / key).resolve()
        else:
            resolved = (self.docs_root / "raw" / key).resolve()
        if not resolved.is_relative_to(self.docs_root.resolve()):
            raise ValueError(f"storage_key escapes docs_root: {storage_key!r}")
        return resolved

    def create_ingest_job(self, document_id: int) -> int:
        active_job_id = self.meta_dao.find_active_job_id(document_id)
        if active_job_id is not None:
            return active_job_id
        self.meta_dao.set_document_status(document_id, "queued")
        return self.meta_dao.create_job(document_id)

    def get_document_id(self, job_id: int) -> int:
        return int(self.meta_dao.get_job(job_id)["document_id"])

    def process_ingest_job(self, job_id: int) -> Dict[str, Any]:
        job = self.meta_dao.get_job(job_id)
        document_id = int(job["document_id"])
        collection_name = "default"
        self.meta_dao.set_document_status(document_id, "indexing")
        self.meta_dao.set_job_running(job_id)

        try:
            ctx = self.meta_dao.get_doc_context(document_id)
            kb_id: Optional[int] = ctx.get("kb_id")
            collection_name = KnowledgeBaseDAO.get_collection_name(kb_id)
            LOGGER.info(
                "ingest_started document_id=%s job_id=%s collection=%s kb_id=%s",
                document_id,
                job_id,
                collection_name,
                kb_id,
            )
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
                metadata = self._build_chunk_metadata(
                    ctx=ctx,
                    source_path=source_path,
                    chunk_engine_name=chunk_engine.name,
                    profile_mime=profile.mime,
                    profile_text_ratio=profile.text_ratio,
                    text=text,
                    chunk_index=idx,
                    total_chunks=len(chunks),
                )
                row = {
                    "text": text,
                    "chunk_index": idx,
                    "metadata": metadata,
                    "source": ctx["storage_key"],
                }
                if vectors is not None:
                    row["embedding"] = vectors[idx]
                payloads.append(row)

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
            return {"ok": True, "job_id": job_id, "status": "success", "chunk_count": len(chunk_ids)}
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
            return {"ok": False, "job_id": job_id, "status": "failed", "error": str(exc)}

    def ingest_document(self, document_id: int) -> Dict[str, Any]:
        job_id = self.create_ingest_job(document_id)
        return self.process_ingest_job(job_id)

    def recover_pending_jobs(self) -> list[int]:
        return self.meta_dao.list_recoverable_job_ids()

    def _build_chunk_metadata(
        self,
        ctx: Dict[str, Any],
        source_path: Path,
        chunk_engine_name: str,
        profile_mime: str,
        profile_text_ratio: float,
        text: str,
        chunk_index: int,
        total_chunks: int,
    ) -> Dict[str, Any]:
        has_page_marker, page_number, cleaned_text = self._parse_page_marker(text)
        heading_hint = self._extract_heading_hint(cleaned_text)
        section_hint = self._extract_section_hint(cleaned_text)
        return {
            "document_id": str(ctx["document_id"]),
            "doc_title": ctx["display_name"],
            "source": ctx["storage_key"],
            "source_type": source_path.suffix.replace(".", "").lower(),
            "mime_type": str(ctx.get("mime_type") or ""),
            "mime_family": source_path.suffix.replace(".", "").lower() or "unknown",
            "category_ids": ",".join(str(c) for c in ctx["category_ids"]),
            "primary_category_id": str(ctx["primary_category_id"] or ""),
            "category_count": str(len(ctx["category_ids"])),
            "has_multiple_categories": "true" if len(ctx["category_ids"]) > 1 else "false",
            "chunk_engine": chunk_engine_name,
            "chunk_profile_mime": profile_mime,
            "chunk_profile_text_ratio": str(profile_text_ratio),
            "embedding_engine": self.embedding_engine.name,
            "chunk_index": str(chunk_index),
            "char_count": str(len(cleaned_text)),
            "token_estimate": str(self._estimate_tokens(cleaned_text)),
            "has_page_marker": "true" if has_page_marker else "false",
            "page_number": str(page_number) if page_number is not None else "",
            "annotation_version": "v1",
            "heading_hint": heading_hint,
            "section_hint": section_hint,
            "content_kind": self._detect_content_kind(cleaned_text, heading_hint),
            "is_first_chunk": "true" if chunk_index == 0 else "false",
            "is_last_chunk": "true" if chunk_index == max(total_chunks - 1, 0) else "false",
            "filename": str(ctx.get("filename") or ""),
            "kb_id": str(ctx.get("kb_id") or ""),
        }

    def _parse_page_marker(self, text: str) -> tuple[bool, Optional[int], str]:
        match = self.PAGE_PATTERN.match(text or "")
        if not match:
            return False, None, text
        return True, int(match.group(1)), text[match.end():].lstrip()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        stripped = text.strip()
        if not stripped:
            return 0
        return max(1, len(stripped) // 4)

    @staticmethod
    def _extract_heading_hint(text: str) -> str:
        for line in text.splitlines():
            candidate = line.strip()
            if candidate:
                return candidate[:120]
        return ""

    @staticmethod
    def _extract_section_hint(text: str) -> str:
        for line in text.splitlines()[:3]:
            candidate = line.strip()
            if not candidate:
                continue
            if re.match(r"^(第[一二三四五六七八九十百]+[章节条]|[一二三四五六七八九十]+、|\d+(\.\d+)+)", candidate):
                return candidate[:120]
        return ""

    @staticmethod
    def _detect_content_kind(text: str, heading_hint: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "empty"
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if heading_hint and len(heading_hint) <= 40 and len(lines) <= 3:
            return "heading"
        if any("|" in line for line in lines[:5]):
            return "table_like"
        if sum(1 for line in lines[:5] if re.match(r"^([\-•*]|\d+[\.)])", line)) >= 2:
            return "list_like"
        return "body"

    def close(self) -> None:
        self.rag_dao.close()
