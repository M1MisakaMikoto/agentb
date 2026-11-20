import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.logging.trace_writer import (  # noqa: E402
    TRACE_LOG_INSTANCE_ENV,
    TRACE_LOG_ROOT_ENV,
    TraceWriter,
)


CHINA_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_trace_writer_preserves_content_in_daily_instance_file(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=CHINA_TIMEZONE))
    writer = TraceWriter(tmp_path, instance_id="agentb-1", clock=clock)

    with writer.open() as stream:
        stream.write("line one\n")
        stream.write("line two\n")

    log_path = (
        tmp_path
        / "2026-11-20"
        / "agentb-1__part-001__start-2026-11-20T10-00-00+0800__active.log"
    )
    assert log_path.read_text(encoding="utf-8") == "line one\nline two\n"


def test_trace_writer_rotates_by_size_with_start_and_end_times(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=CHINA_TIMEZONE))
    writer = TraceWriter(tmp_path, max_bytes=10, instance_id="agentb-1", clock=clock)

    writer.append("12345678")
    clock.value = datetime(2026, 11, 20, 10, 1, 0, tzinfo=CHINA_TIMEZONE)
    writer.append("abcdef")
    clock.value = datetime(2026, 11, 20, 10, 2, 0, tzinfo=CHINA_TIMEZONE)
    writer.append("uvwxyz")

    day_dir = tmp_path / "2026-11-20"
    first = (
        day_dir
        / "agentb-1__part-001__start-2026-11-20T10-00-00+0800"
        "__end-2026-11-20T10-00-00+0800.log"
    )
    second = (
        day_dir
        / "agentb-1__part-002__start-2026-11-20T10-01-00+0800"
        "__end-2026-11-20T10-01-00+0800.log"
    )
    active = (
        day_dir
        / "agentb-1__part-003__start-2026-11-20T10-02-00+0800__active.log"
    )
    assert first.read_text(encoding="utf-8") == "12345678"
    assert second.read_text(encoding="utf-8") == "abcdef"
    assert active.read_text(encoding="utf-8") == "uvwxyz"


def test_trace_writer_switches_daily_directory_at_midnight(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 11, 20, 23, 59, 59, tzinfo=CHINA_TIMEZONE))
    writer = TraceWriter(tmp_path, instance_id="agentb-1", clock=clock)

    writer.append("before midnight\n")
    clock.value = datetime(2026, 11, 21, 0, 0, 1, tzinfo=CHINA_TIMEZONE)
    writer.append("after midnight\n")

    previous = (
        tmp_path
        / "2026-11-20"
        / "agentb-1__part-001__start-2026-11-20T23-59-59+0800"
        "__end-2026-11-20T23-59-59+0800.log"
    )
    current = (
        tmp_path
        / "2026-11-21"
        / "agentb-1__part-001__start-2026-11-21T00-00-01+0800__active.log"
    )
    assert previous.read_text(encoding="utf-8") == "before midnight\n"
    assert current.read_text(encoding="utf-8") == "after midnight\n"


def test_trace_writer_separates_instances_and_resumes_part_numbers(tmp_path: Path) -> None:
    first_clock = MutableClock(
        datetime(2026, 11, 20, 10, 0, 0, tzinfo=CHINA_TIMEZONE)
    )
    second_clock = MutableClock(
        datetime(2026, 11, 20, 10, 0, 1, tzinfo=CHINA_TIMEZONE)
    )
    first_writer = TraceWriter(tmp_path, instance_id="agentb-1", clock=first_clock)
    second_writer = TraceWriter(tmp_path, instance_id="agentb-2", clock=second_clock)

    first_writer.append("instance one\n")
    second_writer.append("instance two\n")
    restarted_writer = TraceWriter(
        tmp_path,
        instance_id="agentb-1",
        clock=MutableClock(datetime(2026, 11, 20, 10, 0, 2, tzinfo=CHINA_TIMEZONE)),
    )
    restarted_writer.append("instance one restarted\n")

    day_dir = tmp_path / "2026-11-20"
    assert len(list(day_dir.glob("agentb-1__part-*.log"))) == 2
    assert len(list(day_dir.glob("agentb-2__part-*.log"))) == 1
    assert (
        day_dir
        / "agentb-1__part-002__start-2026-11-20T10-00-02+0800__active.log"
    ).read_text(encoding="utf-8") == "instance one restarted\n"


def test_trace_writer_close_finalizes_active_segment(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=CHINA_TIMEZONE))
    writer = TraceWriter(tmp_path, instance_id="agentb-1", clock=clock)
    writer.append("content\n")

    writer.close()

    finalized = (
        tmp_path
        / "2026-11-20"
        / "agentb-1__part-001__start-2026-11-20T10-00-00+0800"
        "__end-2026-11-20T10-00-00+0800.log"
    )
    assert finalized.read_text(encoding="utf-8") == "content\n"
    assert not list((tmp_path / "2026-11-20").glob("*__active.log"))


def test_trace_writer_never_deletes_existing_logs(tmp_path: Path) -> None:
    day_dir = tmp_path / "2026-11-20"
    day_dir.mkdir()
    unrelated = day_dir / "legacy_llm_decision_trace.log"
    unrelated.write_text("keep\n", encoding="utf-8")
    older_day_dir = tmp_path / "2026-11-19"
    older_day_dir.mkdir()
    older = older_day_dir / "agentb-1__part-001__old.log"
    older.write_text("old\n", encoding="utf-8")

    writer = TraceWriter(
        tmp_path,
        max_bytes=5,
        instance_id="agentb-1",
        clock=MutableClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=CHINA_TIMEZONE)),
    )
    writer.append("123")
    writer.append("456")

    assert unrelated.read_text(encoding="utf-8") == "keep\n"
    assert older.read_text(encoding="utf-8") == "old\n"


def test_trace_writer_rejects_unwritable_target_shape(tmp_path: Path) -> None:
    root_path = tmp_path / "trace-root"
    root_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OSError):
        TraceWriter(root_path).initialize()


def test_trace_writer_rejects_empty_instance_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid trace log instance id"):
        TraceWriter(tmp_path, instance_id="")


def test_trace_writer_uses_environment_root_and_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_root = tmp_path / "production" / "llm_decision_trace"
    monkeypatch.setenv(TRACE_LOG_ROOT_ENV, str(log_root))
    monkeypatch.setenv(TRACE_LOG_INSTANCE_ENV, "agentb-3")
    writer = TraceWriter(
        clock=MutableClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=CHINA_TIMEZONE))
    )

    writer.append("production trace\n")

    log_path = (
        log_root
        / "2026-11-20"
        / "agentb-3__part-001__start-2026-11-20T10-00-00+0800__active.log"
    )
    assert log_path.read_text(encoding="utf-8") == "production trace\n"
