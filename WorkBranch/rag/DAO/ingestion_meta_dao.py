from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class IngestionMetaDAO:
    """Metadata DAO used by ingestion service only."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        root = Path(__file__).resolve().parents[1]
        self.db_path = db_path or (root / "file_meta.sqlite3")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    def create_job(self, document_id: int) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO ingest_jobs (document_id, status, created_at)
                VALUES (?, 'queued', ?)
                """,
                (document_id, self._now()),
            )
            return int(cur.lastrowid)

    def set_document_status(self, document_id: int, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE documents SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._now(), document_id),
            )

    def set_job_running(self, job_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE ingest_jobs SET status = ?, started_at = ? WHERE id = ?",
                ("running", self._now(), job_id),
            )

    def set_job_finished(self, job_id: int, status: str, error_message: Optional[str] = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE ingest_jobs SET status = ?, error_message = ?, finished_at = ? WHERE id = ?",
                (status, error_message, self._now(), job_id),
            )

    def get_doc_context(self, document_id: int) -> Dict[str, Any]:
        with self._conn() as conn:
            doc = conn.execute(
                """
                SELECT id, storage_key, filename, display_name, mime_type, status, kb_id
                FROM documents
                WHERE id = ?
                """,
                (document_id,),
            ).fetchone()
            if not doc:
                raise ValueError(f"Document {document_id} not found")

            cats = conn.execute(
                """
                SELECT category_id, is_primary
                FROM document_category_map
                WHERE document_id = ?
                ORDER BY is_primary DESC, category_id
                """,
                (document_id,),
            ).fetchall()

        category_ids = [int(r["category_id"]) for r in cats]
        primary_category_id = category_ids[0] if category_ids else None
        return {
            "document_id": int(doc["id"]),
            "storage_key": str(doc["storage_key"]),
            "filename": str(doc["filename"]),
            "display_name": str(doc["display_name"]),
            "mime_type": doc["mime_type"],
            "category_ids": category_ids,
            "primary_category_id": primary_category_id,
            "kb_id": int(doc["kb_id"]) if doc["kb_id"] is not None else None,
        }
