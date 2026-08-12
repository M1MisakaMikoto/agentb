# -*- coding: utf-8 -*-
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest

from service.agent_service.graph.subgraphs.tool_executor import _execute_analyze_image


class _FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root

    def resolve_path(self, workspace_id: str, rel: str):
        candidate = (self.root / rel).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            return (False, f"路径越界: {candidate}")
        return (True, str(candidate))


class _FakeLLM:
    def __init__(self, reply="病害分析结果"):
        self.reply = reply
        self.calls = []

    def chat(self, messages=None, system_prompt=None, **kwargs):
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
        return self.reply


def _make_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True)
    (root / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    return root


def test_missing_image_path(tmp_path):
    ws = _FakeWorkspace(_make_workspace(tmp_path))
    result = _execute_analyze_image(
        {"task": "分析病害"},
        llm_service=_FakeLLM(),
        workspace_service=ws,
        workspace_id="ws1",
        message_context={},
    )
    assert result["error"] and "image_path" in result["error"]
    assert result["result"] is None


def test_missing_task(tmp_path):
    ws = _FakeWorkspace(_make_workspace(tmp_path))
    result = _execute_analyze_image(
        {"image_path": "photo.jpg"},
        llm_service=_FakeLLM(),
        workspace_service=ws,
        workspace_id="ws1",
        message_context={},
    )
    assert result["error"] and "task" in result["error"]


def test_path_traversal_rejected(tmp_path):
    ws = _FakeWorkspace(_make_workspace(tmp_path))
    result = _execute_analyze_image(
        {"image_path": "../secret.jpg", "task": "分析"},
        llm_service=_FakeLLM(),
        workspace_service=ws,
        workspace_id="ws1",
        message_context={},
    )
    assert result["error"] and "路径不允许" in result["error"]


def test_missing_file_returns_error(tmp_path):
    ws = _FakeWorkspace(_make_workspace(tmp_path))
    result = _execute_analyze_image(
        {"image_path": "nope.jpg", "task": "分析"},
        llm_service=_FakeLLM(),
        workspace_service=ws,
        workspace_id="ws1",
        message_context={},
    )
    assert result["error"] and "文件不存在" in result["error"]


def test_success_sends_image_data_url(tmp_path):
    root = _make_workspace(tmp_path)
    ws = _FakeWorkspace(root)
    llm = _FakeLLM(reply="减速带存在病害")
    result = _execute_analyze_image(
        {"image_path": "photo.jpg", "task": "分析病害"},
        llm_service=llm,
        workspace_service=ws,
        workspace_id="ws1",
        message_context={"conversation_id": "conv-1"},
    )
    assert result["error"] is None
    assert result["result"] == "减速带存在病害"
    assert len(llm.calls) == 1
    user_msg = llm.calls[0]["messages"][-1]
    parts = user_msg.get("parts") or []
    image_parts = [p for p in parts if p.get("type") == "image"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"].startswith("data:image/jpeg;base64,")