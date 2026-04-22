import hashlib
import time
from typing import Optional, Dict, Any, List
import threading
import atexit

from .lru_cache import LRUCache
from .sqlite_cache import SQLiteCacheBackend
from .cache_key_generator import CacheKeyGenerator


class CacheInvalidationManager:
    """缓存失效管理器"""
    
    def __init__(self, l1_cache: LRUCache, l2_cache: SQLiteCacheBackend):
        self.l1_cache = l1_cache
        self.l2_cache = l2_cache
        self._cleanup_thread = None
        self._stop_event = threading.Event()
        
        self._start_cleanup_task()
        
        atexit.register(self._stop_cleanup_task)
    
    def _start_cleanup_task(self):
        """启动后台清理任务"""
        def cleanup_loop():
            while not self._stop_event.is_set():
                if self._stop_event.wait(timeout=3600):
                    break
                
                try:
                    self.l2_cache.cleanup_expired()
                except Exception as e:
                    print(f"[CacheCleanup] Error: {e}")
        
        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()
    
    def _stop_cleanup_task(self):
        """停止清理任务"""
        self._stop_event.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)
    
    def invalidate_all(self):
        """手动清除所有缓存"""
        self.l1_cache.clear()
        
        import sqlite3
        with sqlite3.connect(self.l2_cache.db_path) as conn:
            conn.execute("DELETE FROM compression_cache")
            conn.commit()


class CompressionCache:
    """压缩缓存服务（整合L1和L2）"""
    
    def __init__(
        self, 
        settings_service,
        compression_version: str = "v1"
    ):
        self.enabled = settings_service.get("compression:cache_enabled")
        self.ttl = settings_service.get("compression:cache_ttl_seconds")
        self.compression_version = compression_version
        
        self.l1_cache = LRUCache(
            max_size=settings_service.get("compression:l1_cache_size"),
            ttl_seconds=self.ttl
        )
        
        self.l2_cache = SQLiteCacheBackend(
            db_path="data/compression_cache.db"
        )
        
        self.invalidation_manager = CacheInvalidationManager(
            self.l1_cache, 
            self.l2_cache
        )
        
        self._stats = {
            "l1_hits": 0,
            "l2_hits": 0,
            "misses": 0,
            "total_time_saved": 0,
        }
    
    def get(
        self, 
        message: Dict[str, Any], 
        target_ratio: float
    ) -> Optional[Dict[str, Any]]:
        """查询缓存（多级查询）"""
        if not self.enabled:
            return None
        
        start_time = time.time()
        
        cache_key = CacheKeyGenerator.generate(
            message, 
            target_ratio, 
            self.compression_version
        )
        
        result = self.l1_cache.get(cache_key)
        if result is not None:
            self._stats["l1_hits"] += 1
            self._stats["total_time_saved"] += time.time() - start_time
            return result
        
        result = self.l2_cache.get(cache_key)
        if result is not None:
            self.l1_cache.set(cache_key, result)
            self._stats["l2_hits"] += 1
            self._stats["total_time_saved"] += time.time() - start_time
            return result
        
        self._stats["misses"] += 1
        return None
    
    def set(
        self, 
        message: Dict[str, Any], 
        target_ratio: float,
        result: Dict[str, Any],
        original_tokens: int,
        compressed_tokens: int
    ):
        """存储缓存（多级存储）"""
        if not self.enabled:
            return
        
        cache_key = CacheKeyGenerator.generate(
            message, 
            target_ratio, 
            self.compression_version
        )
        
        original_hash = hashlib.sha256(
            CacheKeyGenerator.extract_key_info(message).encode()
        ).hexdigest()
        
        self.l1_cache.set(cache_key, result)
        
        self.l2_cache.set(
            cache_key,
            original_hash,
            result,
            target_ratio,
            original_tokens,
            compressed_tokens,
            self.ttl
        )
    
    def get_hit_rate(self) -> Dict[str, Any]:
        """获取缓存命中率"""
        total = self._stats["l1_hits"] + self._stats["l2_hits"] + self._stats["misses"]
        
        if total == 0:
            return {
                "total_requests": 0,
                "l1_hit_rate": "N/A",
                "l2_hit_rate": "N/A",
                "overall_hit_rate": "N/A",
            }
        
        return {
            "total_requests": total,
            "l1_hits": self._stats["l1_hits"],
            "l2_hits": self._stats["l2_hits"],
            "misses": self._stats["misses"],
            "l1_hit_rate": f"{self._stats['l1_hits'] / total:.2%}",
            "l2_hit_rate": f"{self._stats['l2_hits'] / total:.2%}",
            "overall_hit_rate": f"{(self._stats['l1_hits'] + self._stats['l2_hits']) / total:.2%}",
            "total_time_saved": f"{self._stats['total_time_saved']:.2f}s",
        }
