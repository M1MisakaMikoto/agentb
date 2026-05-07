from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional, Sequence, Set, Tuple


class DocumentMetaDAO:
    """Read current document-category mapping from file_meta.sqlite3."""

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
        finally:
            conn.close()

    def get_doc_category_map(self, document_ids: Sequence[int]) -> Dict[int, Set[int]]:
        unique_ids = sorted({int(d) for d in document_ids if int(d) > 0})
        if not unique_ids:
            return {}

        placeholders = ",".join("?" for _ in unique_ids)
        sql = (
            "SELECT document_id, category_id "
            "FROM document_category_map "
            f"WHERE document_id IN ({placeholders})"
        )
        out: Dict[int, Set[int]] = {}
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(unique_ids)).fetchall()
        for row in rows:
            did = int(row["document_id"])
            cid = int(row["category_id"])
            out.setdefault(did, set()).add(cid)
        return out

    def get_doc_title_map(self, document_ids: Sequence[int]) -> Dict[int, str]:
        unique_ids = sorted({int(d) for d in document_ids if int(d) > 0})
        if not unique_ids:
            return {}

        placeholders = ",".join("?" for _ in unique_ids)
        sql = (
            "SELECT id, display_name "
            "FROM documents "
            f"WHERE id IN ({placeholders})"
        )
        out: Dict[int, str] = {}
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(unique_ids)).fetchall()
        for row in rows:
            did = int(row["id"])
            title = str(row["display_name"] or "").strip()
            if title:
                out[did] = title
        return out

    def get_doc_category_and_title_map(
        self, document_ids: Sequence[int]
    ) -> Tuple[Dict[int, Set[int]], Dict[int, str]]:
        """一次连接同时查分类映射和标题，减少 DB 往返"""
        unique_ids = sorted({int(d) for d in document_ids if int(d) > 0})
        if not unique_ids:
            return {}, {}
        placeholders = ",".join("?" for _ in unique_ids)
        cat_out: Dict[int, Set[int]] = {}
        title_out: Dict[int, str] = {}
        with self._conn() as conn:
            for row in conn.execute(
                f"SELECT document_id, category_id FROM document_category_map WHERE document_id IN ({placeholders})",
                tuple(unique_ids),
            ).fetchall():
                cat_out.setdefault(int(row["document_id"]), set()).add(int(row["category_id"]))
            for row in conn.execute(
                f"SELECT id, display_name FROM documents WHERE id IN ({placeholders})",
                tuple(unique_ids),
            ).fetchall():
                title = str(row["display_name"] or "").strip()
                if title:
                    title_out[int(row["id"])] = title
        return cat_out, title_out
