from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlite_vec
from sqlite_vec import serialize_float32

from rag.logging_utils import get_logger

LOGGER = get_logger(__name__)


class SqliteVecDAO:
    """
    统一的数据访问层，替代旧的 RAG_DAO。
    通过 sqlite-vec 将 chunk 元数据(chunks表)与向量(chunks_vec虚拟表)存放在同一数据库，支持 ACID。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.ensure_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        # Load the sqlite-vec extension
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        
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

    def ensure_schema(self) -> None:
        """初始化表结构"""
        with self._conn() as conn:
            # 存储 Chunk 的原始文本和元数据
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT UNIQUE NOT NULL,
                    document_id TEXT NOT NULL,
                    kb_id INTEGER,
                    chunk_index INTEGER NOT NULL,
                    source TEXT,
                    content_preview TEXT,
                    text TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            # 存储 Chunk 的向量 (通过 rowid 关联 chunks 表)
            # 这里设置向量维度为 1024 适配 BGE-M3
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                    id INTEGER PRIMARY KEY,
                    embedding FLOAT[1024]
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_kb_id ON chunks(kb_id)")

    def add_chunks_batch(
        self,
        chunks: List[Dict[str, Any]],
        kb_id: Optional[int] = None,
        document_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[str]:
        """
        在单一事务中写入文本、元数据和向量。
        保证了 SQLite 级别的事务一致性，不会出现向量孤岛。
        """
        if not chunks:
            return []

        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        chunk_ids_return: List[str] = []

        with self._conn() as conn:
            for idx, item in enumerate(chunks):
                text = item.get("text")
                if not text:
                    continue
                
                chunk_index = item.get("chunk_index", idx)
                chunk_id = item.get("chunk_id") or f"{document_id}_{chunk_index}_{uuid.uuid4().hex[:8]}"
                embedding = item.get("embedding") # list[float] length 1024
                
                if embedding is None:
                    continue

                meta_dict = item.get("metadata") or {}
                meta_json = json.dumps(meta_dict, ensure_ascii=False)
                
                content_preview = text[:200]
                item_source = item.get("source", source)

                # Insert into normal table
                cur = conn.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, kb_id, chunk_index, source, content_preview, text, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (chunk_id, document_id, kb_id, chunk_index, item_source, content_preview, text, meta_json, now)
                )
                rowid = cur.lastrowid
                
                # Insert into vec0 virtual table
                # sqlite_vec requires vectors as BLOBs for optimal performance
                embedding_blob = serialize_float32(embedding)
                conn.execute(
                    """
                    INSERT INTO chunks_vec (id, embedding)
                    VALUES (?, ?)
                    """,
                    (rowid, embedding_blob)
                )
                
                chunk_ids_return.append(chunk_id)

        return chunk_ids_return

    def search(
        self,
        query_vector: List[float],
        kb_id: Optional[int] = None,
        top_k: int = 5,
        where_filter: Optional[Dict[str, Any]] = None,
        document_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        KNN 暴力向量检索，同时支持普通的 SQL Where 过滤条件（kb_id, document_id）。
        """
        query_blob = serialize_float32(query_vector)
        
        sql_clauses = []
        sql_params = []
        
        # 处理过滤条件
        if kb_id is not None:
            sql_clauses.append("c.kb_id = ?")
            sql_params.append(kb_id)
        else:
            # 向后兼容：旧存量无 kb_id，搜 default
            sql_clauses.append("c.kb_id IS NULL")
            
        if document_id is not None:
            sql_clauses.append("c.document_id = ?")
            sql_params.append(document_id)
            
        where_sql = ""
        if sql_clauses:
            where_sql = "AND " + " AND ".join(sql_clauses)
            
        # sqlite-vec knn search: 
        # MATCH queries should look like:
        # SELECT rowid, distance FROM vec_table WHERE embedding MATCH ? ORDER BY distance LIMIT ?
        # We join with with chunks table to filter metadata and get texts
        
        query = f"""
            SELECT 
                c.chunk_id, c.document_id, c.kb_id, c.text, c.metadata, v.distance
            FROM chunks_vec v
            JOIN chunks c ON c.id = v.id
            WHERE v.embedding MATCH ? AND v.k = ? {where_sql}
            ORDER BY v.distance 
        """
        
        search_params = [query_blob, top_k] + sql_params
        
        with self._conn() as conn:
            rows = conn.execute(query, tuple(search_params)).fetchall()
            
        # 兼容 Chroma 的返回值格式：
        # {
        #   "ids": [["id1", "id2"]],
        #   "distances": [[0.1, 0.2]],
        #   "documents": [["text1", "text2"]],
        #   "metadatas": [[{...}, {...}]]
        # }
        ids = []
        distances = []
        documents = []
        metadatas = []
        
        for r in rows:
            ids.append(r["chunk_id"])
            distances.append(float(r["distance"]))
            documents.append(str(r["text"]))
            metadatas.append(json.loads(r["metadata"]) if r["metadata"] else {})
            
        return {
            "ids": [ids],
            "distances": [distances],
            "documents": [documents],
            "metadatas": [metadatas],
        }

    def delete_doc(self, document_id: str) -> int:
        """
        事务内同时清理记录与向量
        """
        with self._conn() as conn:
            # sqlite-vec tables are automatically managed when deleted via id if configured that way, 
            # but usually you manually keep them in sync if you only map rowids.
            # actually we need to explicitly delete from chunks_vec
            
            # Find rowids to delete from vec table first
            rows = conn.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,)).fetchall()
            if not rows:
                return 0
                
            row_ids = [r["id"] for r in rows]
            
            # 批量删除向量
            placeholders = ",".join(["?"] * len(row_ids))
            conn.execute(f"DELETE FROM chunks_vec WHERE id IN ({placeholders})", tuple(row_ids))
            
            # 删除普通记录
            cur = conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            return cur.rowcount

    def get_doc_chunks(self, document_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT chunk_id, chunk_index, document_id, text, metadata FROM chunks WHERE document_id = ? ORDER BY chunk_index ASC",
                (document_id,)
            ).fetchall()
            
        return [
            {
                "chunk_id": r["chunk_id"],
                "chunk_index": r["chunk_index"],
                "document_id": r["document_id"],
                "text": r["text"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
            }
            for r in rows
        ]

    def close(self) -> None:
        pass