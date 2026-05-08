from __future__ import annotations

import queue
import threading
from typing import Optional

from rag.logging_utils import get_logger

LOGGER = get_logger(__name__)


class IngestQueueService:
    def __init__(self, worker, max_queue_size: int = 0) -> None:
        self._worker = worker
        self._queue: queue.Queue[int] = queue.Queue(maxsize=max_queue_size)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active_docs: set[int] = set()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="rag-ingest-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._queue.put(0)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def enqueue(self, job_id: int) -> None:
        if job_id <= 0:
            return
        self._queue.put(job_id)

    def _run(self) -> None:
        while self._running:
            job_id = self._queue.get()
            if job_id <= 0:
                continue

            doc_id = None
            try:
                doc_id = self._worker.get_document_id(job_id)
                if doc_id is not None and not self._mark_active(doc_id):
                    LOGGER.info("ingest_queue_skip_duplicate job_id=%s document_id=%s", job_id, doc_id)
                    continue
                self._worker.process_ingest_job(job_id)
            except Exception:
                LOGGER.exception("ingest_queue_worker_failed job_id=%s", job_id)
            finally:
                if doc_id is not None:
                    self._mark_done(doc_id)
                self._queue.task_done()

    def _mark_active(self, document_id: int) -> bool:
        with self._lock:
            if document_id in self._active_docs:
                return False
            self._active_docs.add(document_id)
            return True

    def _mark_done(self, document_id: int) -> None:
        with self._lock:
            self._active_docs.discard(document_id)
