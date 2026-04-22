import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import threading
import os


class SQLiteCacheBackend:
    """SQLite持久化缓存"""
    
    def __init__(self, db_path: str = "data/compression_cache.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS compression_cache (
                    cache_key TEXT PRIMARY KEY,
                    original_hash TEXT NOT NULL,
                    compressed_result TEXT NOT NULL,
                    target_ratio REAL NOT NULL,
                    original_tokens INTEGER,
                    compressed_tokens INTEGER,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires_at 
                ON compression_cache(expires_at)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_original_hash 
                ON compression_cache(original_hash)
            """)
            
            conn.commit()
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """查询缓存"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT compressed_result, expires_at, access_count
                    FROM compression_cache
                    WHERE cache_key = ?
                """, (key,))
                
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                compressed_result, expires_at, access_count = row
                
                expires_dt = datetime.fromisoformat(expires_at)
                if datetime.now() > expires_dt:
                    conn.execute("""
                        DELETE FROM compression_cache
                        WHERE cache_key = ?
                    """, (key,))
                    conn.commit()
                    return None
                
                conn.execute("""
                    UPDATE compression_cache
                    SET access_count = access_count + 1
                    WHERE cache_key = ?
                """, (key,))
                conn.commit()
                
                return json.loads(compressed_result)
    
    def set(
        self, 
        key: str, 
        original_hash: str,
        value: Dict[str, Any],
        target_ratio: float,
        original_tokens: int,
        compressed_tokens: int,
        ttl_seconds: int = 3600
    ):
        """存储缓存"""
        with self._lock:
            now = datetime.now()
            expires_at = now + timedelta(seconds=ttl_seconds)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO compression_cache
                    (cache_key, original_hash, compressed_result, target_ratio, 
                     original_tokens, compressed_tokens, created_at, expires_at, access_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    key,
                    original_hash,
                    json.dumps(value, ensure_ascii=False),
                    target_ratio,
                    original_tokens,
                    compressed_tokens,
                    now.isoformat(),
                    expires_at.isoformat(),
                ))
                conn.commit()
    
    def cleanup_expired(self):
        """清理过期缓存"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    DELETE FROM compression_cache
                    WHERE expires_at < ?
                """, (datetime.now().isoformat(),))
                conn.commit()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_entries,
                    SUM(access_count) as total_access,
                    AVG(compressed_tokens * 1.0 / original_tokens) as avg_ratio
                FROM compression_cache
                WHERE expires_at > ?
            """, (datetime.now().isoformat(),))
            
            row = cursor.fetchone()
            
            return {
                "total_entries": row[0],
                "total_access": row[1] or 0,
                "avg_compression_ratio": f"{row[2]:.2%}" if row[2] else "N/A",
            }
