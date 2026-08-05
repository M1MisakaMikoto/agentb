from __future__ import annotations

import logging
import os
import signal
import socket
import time
from threading import Event, Thread

from rag.service.ingestion import IngestionService
from rag.service.ingestion.redis_queue import redis_key


LOGGER = logging.getLogger("agentb.rag.worker")


class RagWorker:
    _RELEASE_SCRIPT = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """
    _RENEW_SCRIPT = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('EXPIRE', KEYS[1], ARGV[2])
    end
    return 0
    """

    def __init__(self) -> None:
        try:
            from redis import Redis
        except ImportError as exc:
            raise RuntimeError("redis package is required for the RAG worker") from exc

        redis_url = os.getenv("AGENTB_REDIS_URL", "").strip()
        if not redis_url:
            raise RuntimeError("AGENTB_REDIS_URL is required for the RAG worker")
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._instance_id = (
            os.getenv("AGENTB_INSTANCE_ID", "").strip()
            or f"rag-{socket.gethostname()}"
        )
        self._lease_seconds = max(60, int(os.getenv("AGENTB_RAG_LEASE_SECONDS", "1800")))
        self._queue_key = redis_key("queue:rag:ingest")
        self._processing_key = redis_key("queue:rag:processing")
        self._stop = Event()
        self._service = IngestionService()

    def enqueue_recoverable_jobs(self) -> None:
        for value in self._redis.lrange(self._processing_key, 0, -1):
            self._redis.lpush(self._queue_key, value)
        self._redis.delete(self._processing_key)
        queued = set(self._redis.lrange(self._queue_key, 0, -1))
        for job_id in self._service.recover_pending_jobs():
            value = str(job_id)
            if value not in queued:
                self._redis.lpush(self._queue_key, value)

    def _renew_lease(self, lease_key: str, stop_event: Event) -> None:
        interval = max(5, self._lease_seconds // 3)
        while not stop_event.wait(interval):
            renewed = self._redis.eval(
                self._RENEW_SCRIPT,
                1,
                lease_key,
                self._instance_id,
                self._lease_seconds,
            )
            if not renewed:
                LOGGER.error("rag_lease_lost lease_key=%s", lease_key)
                return

    def stop(self, *_args) -> None:
        self._stop.set()

    def run(self) -> None:
        self._redis.ping()
        self.enqueue_recoverable_jobs()
        LOGGER.info("rag_worker_started instance_id=%s", self._instance_id)
        while not self._stop.is_set():
            job_value = self._redis.brpoplpush(
                self._queue_key, self._processing_key, timeout=2
            )
            if not job_value:
                continue
            lease_key = redis_key(f"lease:rag:ingest:{job_value}")
            leased = self._redis.set(
                lease_key,
                self._instance_id,
                nx=True,
                ex=self._lease_seconds,
            )
            if not leased:
                self._redis.lrem(self._processing_key, 1, job_value)
                self._redis.lpush(self._queue_key, job_value)
                time.sleep(1)
                continue
            lease_stop = Event()
            lease_thread = Thread(
                target=self._renew_lease,
                args=(lease_key, lease_stop),
                name=f"rag-lease-{job_value}",
                daemon=True,
            )
            lease_thread.start()
            try:
                job = self._service.meta_dao.get_job(int(job_value))
                if job["status"] not in {"queued", "running"}:
                    LOGGER.info(
                        "rag_job_skip_terminal job_id=%s status=%s",
                        job_value,
                        job["status"],
                    )
                    continue
                result = self._service.process_ingest_job(int(job_value))
                LOGGER.info("rag_job_finished job_id=%s result=%s", job_value, result)
            except Exception:
                LOGGER.exception("rag_job_crashed job_id=%s", job_value)
                self._redis.lpush(self._queue_key, job_value)
            finally:
                lease_stop.set()
                lease_thread.join(timeout=2)
                self._redis.lrem(self._processing_key, 1, job_value)
                self._redis.eval(
                    self._RELEASE_SCRIPT, 1, lease_key, self._instance_id
                )
        self._service.close()
        self._redis.close()
        LOGGER.info("rag_worker_stopped instance_id=%s", self._instance_id)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = RagWorker()
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
