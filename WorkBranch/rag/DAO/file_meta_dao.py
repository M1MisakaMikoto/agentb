from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from rag.model.do.file.CategoryDO import CategoryDO
from rag.model.do.file.DeleteCategoryResultDO import DeleteCategoryResultDO
from rag.model.do.file.DocumentCategoryDO import DocumentCategoryDO
from rag.model.do.file.DocumentCreateDO import DocumentCreateDO
from rag.model.do.file.DocumentDO import DocumentDO
from rag.model.do.file.DocumentDetailDO import DocumentDetailDO
from rag.model.do.file.IngestJobDO import IngestJobDO
from rag.model.do.file.PagedDocumentDO import PagedDocumentDO


class FileMetaDAO:
    """DAO for categories/documents metadata in file_meta.sqlite3."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
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

    def ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    parent_id INTEGER NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_by INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, parent_id, name),
                    FOREIGN KEY(parent_id) REFERENCES categories(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    filename TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    mime_type TEXT,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    hash_sha256 TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_by INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_category_map (
                    document_id INTEGER NOT NULL,
                    category_id INTEGER NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(document_id, category_id),
                    FOREIGN KEY(document_id) REFERENCES documents(id),
                    FOREIGN KEY(category_id) REFERENCES categories(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ingest_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error_message TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delete_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    storage_key TEXT NOT NULL,
                    collection_name TEXT NOT NULL DEFAULT 'default',
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_status_updated_at ON documents(status, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_tenant_status_updated_at ON documents(tenant_id, status, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ingest_jobs_document_status_created_at ON ingest_jobs(document_id, status, created_at DESC)"
            )
        # 知识库隔离：幂等迁移（knowledge_bases �?+ documents.kb_id 列）
        from rag.DAO.knowledge_base_dao import KnowledgeBaseDAO
        KnowledgeBaseDAO(self.db_path).ensure_schema()

    def list_categories(self, tenant: int = 1, is_city: bool = True) -> list[CategoryDO]:
        with self._conn() as conn:
            if is_city:
                rows = conn.execute(
                    "SELECT id, name, parent_id, created_at FROM categories ORDER BY sort_order, name"
                ).fetchall()
            else:
                # 区县只看本区维护的分类；市级分类（tenant=1）对区县不可见
                rows = conn.execute(
                    "SELECT id, name, parent_id, created_at FROM categories "
                    "WHERE tenant_id = ? ORDER BY sort_order, name",
                    (tenant,),
                ).fetchall()
        return [
            CategoryDO(
                id=int(r["id"]),
                name=str(r["name"]),
                parent_id=int(r["parent_id"]) if r["parent_id"] is not None else None,
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    def create_category(
        self,
        name: str,
        parent_id: Optional[int],
        tenant: int = 1,
        is_city: bool = True,
    ) -> int:
        now = self._now()
        with self._conn() as conn:
            if parent_id is not None:
                parent = conn.execute(
                    "SELECT id, tenant_id FROM categories WHERE id = ?", (parent_id,)
                ).fetchone()
                if not parent:
                    raise ValueError("Parent category not found")
                if not is_city and int(parent["tenant_id"]) != tenant:
                    raise PermissionError("Cannot create a child category under public or another district's category")
            try:
                cur = conn.execute(
                    """
                    INSERT INTO categories (tenant_id, name, parent_id, sort_order, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, 0, 1, ?, ?)
                    """,
                    (tenant, name.strip(), parent_id, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("Category name already exists in this level") from exc
            return int(cur.lastrowid)

    def update_category(
        self,
        category_id: int,
        name: Optional[str],
        parent_id: Optional[int],
        tenant: int = 1,
        is_city: bool = True,
    ) -> None:
        now = self._now()
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
            if not row:
                raise ValueError("Category not found")
            if not is_city and int(row["tenant_id"]) != tenant:
                raise PermissionError("Cannot modify public or another district's category")
            new_name = name.strip() if name is not None else row["name"]
            new_parent = parent_id if parent_id is not None else row["parent_id"]
            if new_parent == category_id:
                raise RuntimeError("parent_id cannot be self")
            if new_parent is not None and new_parent != row["parent_id"]:
                parent = conn.execute(
                    "SELECT id, tenant_id FROM categories WHERE id = ?", (new_parent,)
                ).fetchone()
                if not parent:
                    raise ValueError("Parent category not found")
                if not is_city and int(parent["tenant_id"]) != tenant:
                    raise PermissionError("Cannot move category under public or another district's category")
            try:
                conn.execute(
                    "UPDATE categories SET name = ?, parent_id = ?, updated_at = ? WHERE id = ?",
                    (new_name, new_parent, now, category_id),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("Category name conflict under same parent") from exc

    def delete_category(
        self,
        category_id: int,
        mode: Literal["keep_docs", "unbind_docs", "recursive"],
        tenant: int = 1,
        is_city: bool = True,
    ) -> DeleteCategoryResultDO:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, tenant_id FROM categories WHERE id = ?", (category_id,)
            ).fetchone()
            if not row:
                raise ValueError("Category not found")
            if not is_city and int(row["tenant_id"]) != tenant:
                raise PermissionError("Cannot delete public or another district's category")

            if is_city:
                child = conn.execute(
                    "SELECT id FROM categories WHERE parent_id = ? LIMIT 1", (category_id,)
                ).fetchone()
            else:
                child = conn.execute(
                    "SELECT id FROM categories WHERE parent_id = ? AND tenant_id = ? LIMIT 1",
                    (category_id, tenant),
                ).fetchone()
            if child and mode != "recursive":
                raise RuntimeError("Category has children, use mode=recursive")

            if mode == "recursive":
                queue = [category_id]
                all_ids: list[int] = []
                while queue:
                    cid = queue.pop()
                    all_ids.append(cid)
                    children = conn.execute("SELECT id FROM categories WHERE parent_id = ?", (cid,)).fetchall()
                    queue.extend([int(r["id"]) for r in children])
                for cid in all_ids:
                    conn.execute("DELETE FROM document_category_map WHERE category_id = ?", (cid,))
                    conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
                return DeleteCategoryResultDO(ok=True, deleted_categories=all_ids)

            if mode == "unbind_docs":
                conn.execute("DELETE FROM document_category_map WHERE category_id = ?", (category_id,))

            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            return DeleteCategoryResultDO(ok=True, id=category_id, mode=mode)

    def create_document(
        self,
        display_name: str,
        mime: str,
        size: int,
        hash_sha: str,
        category_id: Optional[int],
        kb_id: Optional[int] = None,
        tenant: int = 1,
    ) -> DocumentCreateDO:
        now = self._now()
        with self._conn() as conn:
            if category_id is not None:
                cat = conn.execute(
                    "SELECT id, tenant_id FROM categories WHERE id = ?", (category_id,)
                ).fetchone()
                if not cat:
                    raise ValueError("Category not found")
                if int(cat["tenant_id"]) not in (1, tenant):
                    raise PermissionError("Cannot bind document to another district's category")
            if kb_id is not None:
                kb = conn.execute("SELECT id FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
                if not kb:
                    raise ValueError(f"知识库不存在：id={kb_id}")

            cur = conn.execute(
                """
                INSERT INTO documents (
                    tenant_id, filename, display_name, storage_key, mime_type, size_bytes, hash_sha256,
                    status, created_by, created_at, updated_at, kb_id
                ) VALUES (?, ?, ?, '', ?, ?, ?, 'queued', 1, ?, ?, ?)
                """,
                (tenant, display_name, display_name, mime, size, hash_sha, now, now, kb_id),
            )
            doc_id = int(cur.lastrowid)
            # 净化文件名，防止路径穿越攻击（如 ../../../etc/passwd）
            safe_name = os.path.basename(display_name.replace("\\", "/"))
            if not safe_name:
                safe_name = "unnamed"
            storage_key = f"{doc_id}_{safe_name}"
            conn.execute("UPDATE documents SET storage_key = ?, updated_at = ? WHERE id = ?", (storage_key, now, doc_id))

            if category_id is not None:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO document_category_map (document_id, category_id, is_primary, created_at)
                    VALUES (?, ?, 1, ?)
                    """,
                    (doc_id, category_id, now),
                )
        return DocumentCreateDO(id=doc_id, storage_key=storage_key)

    def assert_document_manageable(
        self, document_id: int, tenant: int = 1, is_city: bool = True
    ) -> None:
        """区县只能管理自己的文档；市级（1）可管理全部。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, tenant_id FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not row:
                raise ValueError("Document not found")
            if not is_city and int(row["tenant_id"]) != tenant:
                raise PermissionError("Cannot modify public or another district's document")

    def delete_document_row(self, document_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM document_category_map WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def list_documents(
        self,
        category_id: Optional[int],
        keyword: str,
        page: int,
        size: int,
        tenant: int = 1,
        is_city: bool = True,
    ) -> PagedDocumentDO:
        offset = (page - 1) * size
        where = ["d.status NOT IN ('deleted', 'deleting', 'delete_failed')"]
        args: list[Any] = []
        join = ""
        if category_id is not None:
            join = "JOIN document_category_map m ON m.document_id = d.id"
            where.append("m.category_id = ?")
            args.append(category_id)
        if keyword.strip():
            where.append("(d.display_name LIKE ? OR d.filename LIKE ?)")
            like = f"%{keyword.strip()}%"
            args.extend([like, like])
        if not is_city:
            where.append("d.tenant_id = ?")
            args.append(tenant)
        where_sql = " AND ".join(where)

        with self._conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(DISTINCT d.id) AS c FROM documents d {join} WHERE {where_sql}",
                tuple(args),
            ).fetchone()["c"]
            rows = conn.execute(
                f"""
                SELECT DISTINCT d.id, d.display_name, d.filename, d.storage_key, d.mime_type, d.size_bytes, d.status, d.updated_at, d.created_at, d.kb_id, d.tenant_id
                FROM documents d
                {join}
                WHERE {where_sql}
                ORDER BY d.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple(args + [size, offset]),
            ).fetchall()
        items = [
            DocumentDO(
                id=int(r["id"]),
                display_name=str(r["display_name"]),
                filename=str(r["filename"]),
                storage_key=str(r["storage_key"]),
                mime_type=str(r["mime_type"] or ""),
                size_bytes=int(r["size_bytes"] or 0),
                status=str(r["status"] or ""),
                updated_at=str(r["updated_at"] or ""),
                created_at=str(r["created_at"] or ""),
                kb_id=int(r["kb_id"]) if r["kb_id"] is not None else None,
                tenant_id=int(r["tenant_id"]) if r["tenant_id"] is not None else None,
            )
            for r in rows
        ]
        return PagedDocumentDO(page=page, size=size, total=int(total), items=items)

    def get_document_detail(
        self, document_id: int, tenant: int = 1, is_city: bool = True
    ) -> DocumentDetailDO:
        with self._conn() as conn:
            if is_city:
                doc = conn.execute(
                    "SELECT * FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
            else:
                doc = conn.execute(
                    "SELECT * FROM documents WHERE id = ? AND tenant_id = ?",
                    (document_id, tenant),
                ).fetchone()
            if not doc:
                raise ValueError("Document not found")
            if is_city:
                cats = conn.execute(
                    """
                    SELECT c.id, c.name, m.is_primary
                    FROM document_category_map m
                    JOIN categories c ON c.id = m.category_id
                    WHERE m.document_id = ?
                    ORDER BY m.is_primary DESC, c.name
                    """,
                    (document_id,),
                ).fetchall()
            else:
                cats = conn.execute(
                    """
                    SELECT c.id, c.name, m.is_primary
                    FROM document_category_map m
                    JOIN categories c ON c.id = m.category_id
                    WHERE m.document_id = ? AND c.tenant_id = ?
                    ORDER BY m.is_primary DESC, c.name
                    """,
                    (document_id, tenant),
                ).fetchall()
        document_do = DocumentDO(
            id=int(doc["id"]),
            display_name=str(doc["display_name"]),
            filename=str(doc["filename"]),
            storage_key=str(doc["storage_key"]),
            mime_type=str(doc["mime_type"] or ""),
            size_bytes=int(doc["size_bytes"] or 0),
            status=str(doc["status"] or ""),
            updated_at=str(doc["updated_at"] or ""),
            created_at=str(doc["created_at"] or ""),
            kb_id=int(doc["kb_id"]) if doc["kb_id"] is not None else None,
            tenant_id=int(doc["tenant_id"]) if doc["tenant_id"] is not None else None,
        )
        category_dos = [
            DocumentCategoryDO(
                id=int(c["id"]),
                name=str(c["name"]),
                is_primary=bool(c["is_primary"]),
            )
            for c in cats
        ]
        return DocumentDetailDO(document=document_do, categories=category_dos)

    def update_document_name(
        self, document_id: int, display_name: str, tenant: int = 1, is_city: bool = True
    ) -> None:
        now = self._now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, tenant_id FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not row:
                raise ValueError("Document not found")
            if not is_city and int(row["tenant_id"]) != tenant:
                raise PermissionError("Cannot rename public or another district's document")
            conn.execute(
                "UPDATE documents SET display_name = ?, updated_at = ? WHERE id = ?",
                (display_name.strip(), now, document_id),
            )

    def mount_document_category(
        self, document_id: int, category_id: int, tenant: int = 1, is_city: bool = True
    ) -> None:
        now = self._now()
        with self._conn() as conn:
            doc = conn.execute(
                "SELECT id, tenant_id FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            cat = conn.execute(
                "SELECT id, tenant_id FROM categories WHERE id = ?", (category_id,)
            ).fetchone()
            if not doc or not cat:
                raise ValueError("Document or category not found")
            if not is_city:
                if int(doc["tenant_id"]) != tenant:
                    raise PermissionError("Cannot modify public or another district's document")
                if int(cat["tenant_id"]) not in (1, tenant):
                    raise PermissionError("Cannot bind to another district's category")
            conn.execute(
                """
                INSERT OR IGNORE INTO document_category_map (document_id, category_id, is_primary, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (document_id, category_id, now),
            )

    def unmount_document_category(
        self, document_id: int, category_id: int, tenant: int = 1, is_city: bool = True
    ) -> None:
        with self._conn() as conn:
            if not is_city:
                doc = conn.execute(
                    "SELECT id, tenant_id FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                if not doc:
                    raise ValueError("Document not found")
                if int(doc["tenant_id"]) != tenant:
                    raise PermissionError("Cannot modify public or another district's document")
            conn.execute(
                "DELETE FROM document_category_map WHERE document_id = ? AND category_id = ?",
                (document_id, category_id),
            )

    def set_primary_category(
        self, document_id: int, category_id: int, tenant: int = 1, is_city: bool = True
    ) -> None:
        with self._conn() as conn:
            if not is_city:
                doc = conn.execute(
                    "SELECT id, tenant_id FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                if not doc:
                    raise ValueError("Document not found")
                if int(doc["tenant_id"]) != tenant:
                    raise PermissionError("Cannot modify public or another district's document")
            row = conn.execute(
                "SELECT 1 FROM document_category_map WHERE document_id = ? AND category_id = ?",
                (document_id, category_id),
            ).fetchone()
            if not row:
                raise ValueError("Mapping not found")
            conn.execute("UPDATE document_category_map SET is_primary = 0 WHERE document_id = ?", (document_id,))
            conn.execute(
                "UPDATE document_category_map SET is_primary = 1 WHERE document_id = ? AND category_id = ?",
                (document_id, category_id),
            )

    def get_ingest_job(self, job_id: int) -> IngestJobDO:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, document_id, status, error_message, started_at, finished_at, created_at FROM ingest_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise ValueError("Job not found")
        return IngestJobDO(
            id=int(row["id"]),
            document_id=int(row["document_id"]),
            status=str(row["status"]),
            error_message=str(row["error_message"]) if row["error_message"] is not None else None,
            started_at=str(row["started_at"]) if row["started_at"] is not None else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
            created_at=str(row["created_at"]),
        )
