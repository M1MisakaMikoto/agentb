from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class MetadataDAO:
    """Store business metadata in SQLite (not in Chroma internal DB)."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        self.db_path = db_path or (base_dir / "rag_metadata.sqlite3")
        self._local = threading.local()
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """返回当前线程的SQLite 连接，不存在则新建"""
        if not hasattr(self._local, "connection"):
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.connection = conn
        return self._local.connection

    def _ensure_schema(self) -> None:
        if self._needs_migration():
            self._migrate_to_chunk_pk()

        self._get_connection().execute(
            """
            CREATE TABLE IF NOT EXISTS rag_chunks (
                chunk_id TEXT PRIMARY KEY,
                collection_name TEXT NOT NULL,
                document_id TEXT,
                chunk_index INTEGER,
                source TEXT,
                content_preview TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._get_connection().execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc_collection
            ON rag_chunks (document_id, collection_name)
            """
        )
        self._get_connection().commit()

    def _needs_migration(self) -> bool:
        rows = self._get_connection().execute("PRAGMA table_info(rag_chunks)").fetchall()
        if not rows:
            return False
        columns = {row[1] for row in rows}
        return "id" in columns

    def _migrate_to_chunk_pk(self) -> None:
        conn = self._get_connection()
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE rag_chunks RENAME TO rag_chunks_old")
        conn.execute(
            """
            CREATE TABLE rag_chunks (
                chunk_id TEXT PRIMARY KEY,
                collection_name TEXT NOT NULL,
                document_id TEXT,
                chunk_index INTEGER,
                source TEXT,
                content_preview TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO rag_chunks (
                chunk_id, collection_name, document_id, source, content_preview, created_at
            )
            SELECT
                chunk_id, collection_name, document_id, source, content_preview, created_at
            FROM rag_chunks_old
            """
        )
        conn.execute("DROP TABLE rag_chunks_old")
        conn.execute("COMMIT")

    def upsert_chunk_metadata(
        self,
        chunk_id: str,
        collection_name: str,
        document_id: Optional[str] = None,
        chunk_index: Optional[int] = None,
        source: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO rag_chunks (
                chunk_id, collection_name, document_id, chunk_index, source, content_preview
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                collection_name = excluded.collection_name,
                document_id = excluded.document_id,
                chunk_index = excluded.chunk_index,
                source = excluded.source,
                content_preview = excluded.content_preview
            """,
            (chunk_id, collection_name, document_id, chunk_index, source, content_preview),
        )
        conn.commit()

    def upsert_chunk_metadata_many(
        self,
        rows: List[Dict[str, Any]],
    ) -> None:
        """批量 upsert，单次事务完成，比逐条调用性能更好
        每个 row 需包含: chunk_id, collection_name
        可选 document_id, chunk_index, source, content_preview
        """
        if not rows:
            return
        conn = self._get_connection()
        conn.executemany(
            """
            INSERT INTO rag_chunks (
                chunk_id, collection_name, document_id, chunk_index, source, content_preview
            )
            VALUES (:chunk_id, :collection_name, :document_id, :chunk_index, :source, :content_preview)
            ON CONFLICT(chunk_id) DO UPDATE SET
                collection_name = excluded.collection_name,
                document_id = excluded.document_id,
                chunk_index = excluded.chunk_index,
                source = excluded.source,
                content_preview = excluded.content_preview
            """,
            rows,
        )
        conn.commit()

    def delete_chunk_metadata(self, chunk_id: str) -> None:
        conn = self._get_connection()
        conn.execute("DELETE FROM rag_chunks WHERE chunk_id = ?", (chunk_id,))
        conn.commit()

    def delete_chunk_metadata_many(self, chunk_ids: List[str]) -> int:
        if not chunk_ids:
            return 0
        placeholders = ",".join("?" for _ in chunk_ids)
        conn = self._get_connection()
        cursor = conn.execute(
            f"DELETE FROM rag_chunks WHERE chunk_id IN ({placeholders})",
            tuple(chunk_ids),
        )
        conn.commit()
        return cursor.rowcount if cursor.rowcount is not None else 0

    def get_chunk_ids_by_document(self, document_id: str, collection_name: str) -> List[str]:
        rows = self._get_connection().execute(
            """
            SELECT chunk_id
            FROM rag_chunks
            WHERE document_id = ? AND collection_name = ?
            ORDER BY chunk_index ASC, created_at ASC
            """,
            (document_id, collection_name),
        ).fetchall()
        return [row["chunk_id"] for row in rows]

    def get_doc_chunks(self, document_id: str, collection_name: str) -> List[Dict[str, Any]]:
        rows = self._get_connection().execute(
            """
            SELECT chunk_id, collection_name, document_id, chunk_index, source, content_preview, created_at
            FROM rag_chunks
            WHERE document_id = ? AND collection_name = ?
            ORDER BY chunk_index ASC, created_at ASC
            """,
            (document_id, collection_name),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_doc(self, document_id: str, collection_name: str) -> int:
        conn = self._get_connection()
        cursor = conn.execute(
            "DELETE FROM rag_chunks WHERE document_id = ? AND collection_name = ?",
            (document_id, collection_name),
        )
        conn.commit()
        return cursor.rowcount if cursor.rowcount is not None else 0

    def close(self) -> None:
        if hasattr(self._local, "connection"):
            self._local.connection.close()
            del self._local.connection
