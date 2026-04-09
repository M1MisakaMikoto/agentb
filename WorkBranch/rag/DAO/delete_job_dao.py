from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from rag.model.do.file.DeleteJobDO import DeleteJobDO
from rag.service.delete_state import DeleteState


class DeleteJobDAO:
    """Persistence for delete WAL jobs."""

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

    def find_active_job_id(self, document_id: int) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM delete_jobs
                WHERE document_id = ?
                  AND state != ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (document_id, DeleteState.completed.value),
            ).fetchone()
            return int(row["id"]) if row else None

    def create_job(self, document_id: int, storage_key: str, collection_name: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO delete_jobs (
                    document_id, storage_key, collection_name, state,
                    attempts, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (document_id, storage_key, collection_name, DeleteState.pending.value, self._now(), self._now()),
            )
            return int(cur.lastrowid)

    def get_job(self, job_id: int) -> DeleteJobDO:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, document_id, storage_key, collection_name, state, attempts,
                       last_error, started_at, finished_at, created_at, updated_at
                FROM delete_jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Delete job {job_id} not found")
            return DeleteJobDO(
                id=int(row["id"]),
                document_id=int(row["document_id"]),
                storage_key=str(row["storage_key"]),
                collection_name=str(row["collection_name"] or "default"),
                state=str(row["state"]),
                attempts=int(row["attempts"] or 0),
                last_error=str(row["last_error"]) if row["last_error"] is not None else None,
                started_at=str(row["started_at"]) if row["started_at"] is not None else None,
                finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )

    def set_job_running(self, job_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE delete_jobs
                SET attempts = attempts + 1,
                    last_error = NULL,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ?
                  AND state != ?
                """,
                (self._now(), self._now(), job_id, DeleteState.completed.value),
            )
            return (cur.rowcount or 0) > 0

    def transition_state(
        self,
        job_id: int,
        from_state: DeleteState,
        to_state: DeleteState,
        last_error: Optional[str] = None,
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE delete_jobs
                SET state = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND state = ?
                """,
                (to_state.value, last_error, self._now(), job_id, from_state.value),
            )
            return (cur.rowcount or 0) > 0

    def complete_from_state(self, job_id: int, from_state: DeleteState) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE delete_jobs
                SET state = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND state = ?
                """,
                (DeleteState.completed.value, self._now(), self._now(), job_id, from_state.value),
            )
            return (cur.rowcount or 0) > 0
