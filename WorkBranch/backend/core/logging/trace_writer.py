from __future__ import annotations

import io
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


TRACE_LOG_PATH_ENV = "LLM_TRACE_LOG_PATH"
TRACE_LOG_MAX_BYTES = 10 * 1024 * 1024
TRACE_LOG_BACKUP_COUNT = 10
DEFAULT_TRACE_LOG_PATH = Path(__file__).resolve().parents[2] / "llm_decision_trace.log"


class TraceWriter:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_bytes: int = TRACE_LOG_MAX_BYTES,
        backup_count: int = TRACE_LOG_BACKUP_COUNT,
    ) -> None:
        self.path = Path(path) if path is not None else Path(
            os.environ.get(TRACE_LOG_PATH_ENV, DEFAULT_TRACE_LOG_PATH)
        )
        assert max_bytes > 0
        assert backup_count > 0
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.RLock()
        self._initialized = False

    def initialize(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                pass
            self._initialized = True

    @contextmanager
    def open(self) -> Iterator[TextIO]:
        buffer = io.StringIO()
        try:
            yield buffer
        finally:
            content = buffer.getvalue()
            if content:
                self.append(content)

    def append(self, content: str) -> None:
        assert isinstance(content, str)
        encoded_size = len(content.encode("utf-8"))
        with self._lock:
            if not self._initialized:
                self.initialize()
            current_size = self.path.stat().st_size
            if current_size > 0 and current_size + encoded_size > self._max_bytes:
                self._rotate()
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()

    def _rotate(self) -> None:
        for index in range(self._backup_count, 0, -1):
            source = self.path if index == 1 else self._archive_path(index - 1)
            target = self._archive_path(index)
            if target.exists():
                target.unlink()
            if source.exists():
                source.replace(target)

    def _archive_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{index}")


_trace_writer = TraceWriter()


def initialize_trace_writer() -> None:
    _trace_writer.initialize()


@contextmanager
def open_trace_log() -> Iterator[TextIO]:
    with _trace_writer.open() as stream:
        yield stream
