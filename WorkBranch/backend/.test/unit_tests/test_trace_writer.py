import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.logging.trace_writer import TRACE_LOG_PATH_ENV, TraceWriter


def test_trace_writer_preserves_content(tmp_path: Path) -> None:
    log_path = tmp_path / "llm_decision_trace.log"
    writer = TraceWriter(log_path)

    with writer.open() as stream:
        stream.write("line one\n")
        stream.write("line two\n")

    assert log_path.read_text(encoding="utf-8") == "line one\nline two\n"


def test_trace_writer_rotates_before_size_limit(tmp_path: Path) -> None:
    log_path = tmp_path / "llm_decision_trace.log"
    writer = TraceWriter(log_path, max_bytes=10, backup_count=2)

    writer.append("12345678")
    writer.append("abcdef")
    writer.append("uvwxyz")

    assert log_path.read_text(encoding="utf-8") == "uvwxyz"
    assert (tmp_path / "llm_decision_trace.log.1").read_text(encoding="utf-8") == "abcdef"
    assert (tmp_path / "llm_decision_trace.log.2").read_text(encoding="utf-8") == "12345678"


def test_trace_writer_rejects_unwritable_target_shape(tmp_path: Path) -> None:
    log_path = tmp_path / "llm_decision_trace.log"
    log_path.mkdir()

    with pytest.raises(OSError):
        TraceWriter(log_path).initialize()


def test_trace_writer_uses_environment_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "production" / "llm_decision_trace.log"
    monkeypatch.setenv(TRACE_LOG_PATH_ENV, str(log_path))

    writer = TraceWriter()
    writer.append("production trace\n")

    assert log_path.read_text(encoding="utf-8") == "production trace\n"
