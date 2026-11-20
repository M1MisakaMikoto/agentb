from __future__ import annotations

import io
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, TextIO


TRACE_LOG_ROOT_ENV = "LLM_TRACE_LOG_ROOT"
TRACE_LOG_INSTANCE_ENV = "AGENTB_INSTANCE_ID"
TRACE_LOG_MAX_BYTES = 10 * 1024 * 1024
TRACE_LOG_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_TRACE_LOG_INSTANCE = "local"
DEFAULT_TRACE_LOG_ROOT = Path(__file__).resolve().parents[4] / "logs" / "llm_decision_trace"


def _china_now() -> datetime:
    return datetime.now(TRACE_LOG_TIMEZONE)


@dataclass
class _TraceSegment:
    path: Path
    local_date: date
    part_number: int
    started_at: datetime
    last_write_at: datetime


class TraceWriter:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_bytes: int = TRACE_LOG_MAX_BYTES,
        instance_id: str | None = None,
        clock: Callable[[], datetime] = _china_now,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        configured_root = root if root is not None else os.environ.get(TRACE_LOG_ROOT_ENV)
        if configured_root is not None and not str(configured_root).strip():
            raise ValueError("Trace log root must not be empty")
        self.root = Path(configured_root) if configured_root is not None else DEFAULT_TRACE_LOG_ROOT

        configured_instance = (
            instance_id if instance_id is not None else os.environ.get(TRACE_LOG_INSTANCE_ENV)
        )
        self.instance_id = (
            DEFAULT_TRACE_LOG_INSTANCE
            if configured_instance is None
            else configured_instance
        )
        if re.fullmatch(r"[A-Za-z0-9._-]+", self.instance_id) is None:
            raise ValueError(f"Invalid trace log instance id: {self.instance_id!r}")

        self._max_bytes = max_bytes
        self._clock = clock
        self._lock = threading.RLock()
        self._initialized = False
        self._active: _TraceSegment | None = None

    def initialize(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
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

            now = self._local_now()
            if self._active is None:
                self._start_segment(now)
            elif self._active.local_date != now.date():
                self._finalize_active()
                self._start_segment(now)
            else:
                current_size = self._active.path.stat().st_size
                if current_size > 0 and current_size + encoded_size > self._max_bytes:
                    self._finalize_active()
                    self._start_segment(now)

            assert self._active is not None
            with self._active.path.open("a", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
            self._active.last_write_at = now

    def close(self) -> None:
        with self._lock:
            if self._active is not None:
                self._finalize_active()
            self._initialized = False

    def _local_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Trace writer clock must return a timezone-aware datetime")
        return now.astimezone(TRACE_LOG_TIMEZONE)

    def _start_segment(self, now: datetime) -> None:
        local_date = now.date()
        day_dir = self.root / local_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)

        part_number = self._next_part_number(day_dir)
        start_token = now.strftime("%Y-%m-%dT%H-%M-%S%z")
        path = day_dir / (
            f"{self.instance_id}__part-{part_number:03d}"
            f"__start-{start_token}__active.log"
        )
        with path.open("x", encoding="utf-8"):
            pass
        self._active = _TraceSegment(
            path=path,
            local_date=local_date,
            part_number=part_number,
            started_at=now,
            last_write_at=now,
        )

    def _next_part_number(self, day_dir: Path) -> int:
        pattern = re.compile(
            rf"^{re.escape(self.instance_id)}__part-(\d{{3,}})__start-.*__"
            rf"(?:active|end-\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}}\+0800)\.log$"
        )
        part_numbers: list[int] = []
        for path in day_dir.glob(f"{self.instance_id}__part-*.log"):
            match = pattern.fullmatch(path.name)
            if match is not None:
                part_numbers.append(int(match.group(1)))
        return max(part_numbers, default=0) + 1

    def _finalize_active(self) -> None:
        assert self._active is not None
        assert self._active.path.exists()
        end_token = self._active.last_write_at.strftime("%Y-%m-%dT%H-%M-%S%z")
        finalized_name = self._active.path.name.removesuffix("__active.log")
        finalized_path = self._active.path.with_name(
            f"{finalized_name}__end-{end_token}.log"
        )
        if finalized_path.exists():
            raise FileExistsError(f"Trace log target already exists: {finalized_path}")
        self._active.path.replace(finalized_path)
        self._active = None


_trace_writer = TraceWriter()


def initialize_trace_writer() -> None:
    _trace_writer.initialize()


@contextmanager
def open_trace_log() -> Iterator[TextIO]:
    with _trace_writer.open() as stream:
        yield stream


def shutdown_trace_writer() -> None:
    _trace_writer.close()
