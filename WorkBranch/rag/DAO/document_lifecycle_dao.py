from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional


class DocumentLifecycleDAO:
    """Write-side document lifecycle persistence."""

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

    def get_storage_key(self, document_id: int) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, storage_key FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Document {document_id} not found")
            return str(row["storage_key"])

    def mark_deleting(self, document_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE documents SET status = 'deleting', updated_at = ? WHERE id = ?",
                (self._now(), document_id),
            )

    def mark_deleted_and_unbind(self, document_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE documents SET status = 'deleted', updated_at = ? WHERE id = ?",
                (self._now(), document_id),
            )
            conn.execute("DELETE FROM document_category_map WHERE document_id = ?", (document_id,))

    def mark_delete_failed(self, document_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE documents SET status = 'delete_failed', updated_at = ? WHERE id = ?",
                (self._now(), document_id),
            )
