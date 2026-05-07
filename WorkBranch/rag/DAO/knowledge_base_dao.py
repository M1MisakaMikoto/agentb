from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from rag.model.do.file.KnowledgeBaseDO import KnowledgeBaseDO


class KnowledgeBaseDAO:
    """
    知识库元数据 DAO。
    负责 knowledge_bases 表的 CRUD 以及 documents.kb_id 列的 schema 迁移。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

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

    # ------------------------------------------------------------------
    # Schema 初始化与迁移（幂等）
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """建 knowledge_bases 表，并为 documents 表幂等添加 kb_id 列。"""
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL UNIQUE,
                    description TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
                """
            )
            # 幂等：documents 表添加 kb_id 列（旧表不存在该列时才执行）
            existing = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
            if "kb_id" not in existing:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN kb_id INTEGER REFERENCES knowledge_bases(id)"
                )

    # ------------------------------------------------------------------
    # 知识库 CRUD
    # ------------------------------------------------------------------

    def create(self, name: str, description: Optional[str] = None) -> int:
        now = self._now()
        with self._conn() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO knowledge_bases (name, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name.strip(), description, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError(f"知识库名称已存在：{name!r}") from exc
            return int(cur.lastrowid)

    def list_all(self) -> List[KnowledgeBaseDO]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, description, created_at, updated_at FROM knowledge_bases ORDER BY id ASC"
            ).fetchall()
        return [
            KnowledgeBaseDO(
                id=int(r["id"]),
                name=str(r["name"]),
                description=str(r["description"]) if r["description"] is not None else None,
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
            )
            for r in rows
        ]

    def get(self, kb_id: int) -> KnowledgeBaseDO:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, description, created_at, updated_at FROM knowledge_bases WHERE id = ?",
                (kb_id,),
            ).fetchone()
        if not row:
            raise ValueError(f"知识库不存在：id={kb_id}")
        return KnowledgeBaseDO(
            id=int(row["id"]),
            name=str(row["name"]),
            description=str(row["description"]) if row["description"] is not None else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def update(self, kb_id: int, name: Optional[str] = None, description: Optional[str] = None) -> None:
        now = self._now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, description FROM knowledge_bases WHERE id = ?", (kb_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"知识库不存在：id={kb_id}")
            new_name = name.strip() if name is not None else row["name"]
            new_desc = description if description is not None else row["description"]
            try:
                conn.execute(
                    "UPDATE knowledge_bases SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                    (new_name, new_desc, now, kb_id),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError(f"知识库名称已存在：{new_name!r}") from exc

    def delete(self, kb_id: int) -> None:
        with self._conn() as conn:
            row = conn.execute("SELECT id FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
            if not row:
                raise ValueError(f"知识库不存在：id={kb_id}")
            # 检查是否仍有文档归属
            doc_count = conn.execute(
                "SELECT COUNT(*) AS c FROM documents WHERE kb_id = ? AND status NOT IN ('deleted')",
                (kb_id,),
            ).fetchone()["c"]
            if doc_count > 0:
                raise RuntimeError(
                    f"知识库 id={kb_id} 下仍有 {doc_count} 个文档，请先删除或迁移文档后再删除知识库"
                )
            conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def get_collection_name(kb_id: Optional[int]) -> str:
        """根据 kb_id 返回对应的 Chroma collection 名称。"""
        if kb_id is None:
            return "default"
        return f"kb_{kb_id}"