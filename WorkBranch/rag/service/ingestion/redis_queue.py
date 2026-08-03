from __future__ import annotations

import os


def redis_key(suffix: str) -> str:
    prefix = os.getenv("AGENTB_REDIS_PREFIX", "agentb:dev:").strip() or "agentb:dev:"
    if not prefix.endswith(":"):
        prefix += ":"
    return f"{prefix}{suffix}"


class RedisIngestQueueProducer:
    def __init__(self, redis_url: str | None = None) -> None:
        try:
            from redis import Redis
        except ImportError as exc:
            raise RuntimeError("redis package is required for the RAG queue") from exc
        url = redis_url or os.getenv("AGENTB_REDIS_URL", "").strip()
        if not url:
            raise RuntimeError("AGENTB_REDIS_URL is required for the RAG queue")
        self._redis = Redis.from_url(url, decode_responses=True)
        self._queue_key = redis_key("queue:rag:ingest")

    def enqueue(self, job_id: int) -> None:
        if job_id > 0:
            self._redis.lpush(self._queue_key, str(job_id))

    def close(self) -> None:
        self._redis.close()
