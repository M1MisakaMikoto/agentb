import asyncio
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.agent_service import (
    AgentService,
    Conversation,
    ConversationStatus,
)
from service.agent_service.tools import document_tools
from service.agent_service.tools.pandoc_cache import PandocConversationCache


def _fake_pandoc(monkeypatch, text):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=text, stderr=""))
    monkeypatch.setattr(document_tools, "_find_pandoc", lambda: "pandoc")
    monkeypatch.setattr(document_tools.subprocess, "run", run)
    return run


def test_document_read_and_search_share_cache_within_conversation(tmp_path, monkeypatch):
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")
    run = _fake_pandoc(monkeypatch, "heading\\ntarget value\\ntail\\n")

    read_result = document_tools.execute_document(
        {"operation": "r", "file_path": str(path), "max_length": 7},
        conversation_id="conversation-a",
    )
    search_result = document_tools.execute_document(
        {"operation": "s", "file_path": str(path), "pattern": "target"},
        conversation_id="conversation-a",
    )

    assert read_result["error"] is None
    assert search_result["error"] is None
    assert search_result["result"]["total_matches"] == 1
    assert run.call_count == 1


def test_cache_isolated_by_conversation_and_invalidated_by_file_change(tmp_path, monkeypatch):
    path = tmp_path / "report.docx"
    path.write_bytes(b"v1")
    run = _fake_pandoc(monkeypatch, "target\\n")
    args = {"operation": "r", "file_path": str(path)}

    document_tools.execute_document(args, conversation_id="conversation-a")
    document_tools.execute_document(args, conversation_id="conversation-b")
    path.write_bytes(b"version-two")
    document_tools.execute_document(args, conversation_id="conversation-a")

    assert run.call_count == 3


def test_parallel_cache_loads_same_file_once(tmp_path):
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")
    cache = PandocConversationCache()
    loader = Mock(side_effect=lambda: (time.sleep(0.05), "content")[1])

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda _: cache.get_or_load("conversation-a", str(path), loader),
            range(3),
        ))

    assert results == ["content"] * 3
    assert loader.call_count == 1


def test_clear_removes_only_target_conversation(tmp_path):
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")
    cache = PandocConversationCache()
    loader_a = Mock(return_value="a")
    loader_b = Mock(return_value="b")

    cache.get_or_load("conversation-a", str(path), loader_a)
    cache.get_or_load("conversation-b", str(path), loader_b)
    cache.clear_conversation("conversation-a")
    cache.get_or_load("conversation-a", str(path), loader_a)
    cache.get_or_load("conversation-b", str(path), loader_b)

    assert loader_a.call_count == 2
    assert loader_b.call_count == 1


def test_clear_during_load_prevents_stale_reinsertion(tmp_path):
    path = tmp_path / "report.docx"
    path.write_bytes(b"docx")
    cache = PandocConversationCache()
    started = Event()
    release = Event()

    def slow_loader():
        started.set()
        assert release.wait(timeout=2)
        return "stale"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            cache.get_or_load,
            "conversation-a",
            str(path),
            slow_loader,
        )
        assert started.wait(timeout=2)
        cache.clear_conversation("conversation-a")
        release.set()
        assert future.result(timeout=2) == "stale"

    fresh_loader = Mock(return_value="fresh")
    assert cache.get_or_load(
        "conversation-a",
        str(path),
        fresh_loader,
    ) == "fresh"
    fresh_loader.assert_called_once_with()


@pytest.mark.parametrize(
    ("task_result", "raises", "expected_status"),
    [
        ({}, None, ConversationStatus.COMPLETED),
        (None, RuntimeError("failed"), ConversationStatus.FAILED),
        (None, asyncio.CancelledError(), ConversationStatus.CANCELLED),
    ],
)
def test_terminal_task_completion_clears_cache(task_result, raises, expected_status):
    service = AgentService.__new__(AgentService)
    conversation_id = "conversation-a"
    conversation = Conversation(
        id=conversation_id,
        workspace_id="workspace",
        session_id="session",
        status=ConversationStatus.RUNNING,
    )
    service._conversations = {conversation_id: conversation}
    service._clear_pandoc_cache = Mock()
    task = Mock()
    if raises is None:
        task.result.return_value = task_result
    else:
        task.result.side_effect = raises

    service._on_task_complete(conversation_id, task)

    assert conversation.status == expected_status
    service._clear_pandoc_cache.assert_called_once_with(conversation_id)


def test_awaiting_user_input_retains_cache():
    service = AgentService.__new__(AgentService)
    conversation_id = "conversation-a"
    conversation = Conversation(
        id=conversation_id,
        workspace_id="workspace",
        session_id="session",
        status=ConversationStatus.RUNNING,
    )
    service._conversations = {conversation_id: conversation}
    service._clear_pandoc_cache = Mock()
    task = Mock()
    task.result.return_value = {"status": "awaiting_user_input"}

    service._on_task_complete(conversation_id, task)

    assert conversation.status == ConversationStatus.AWAITING_USER_INPUT
    service._clear_pandoc_cache.assert_not_called()


def test_cancel_conversation_clears_cache():
    service = AgentService.__new__(AgentService)
    conversation_id = "conversation-a"
    task = Mock()
    task.done.return_value = False
    service._conversations = {
        conversation_id: Conversation(
            id=conversation_id,
            workspace_id="workspace",
            session_id="session",
            status=ConversationStatus.RUNNING,
            task=task,
        )
    }
    service._close_conversation_http_clients = Mock()
    service._clear_pandoc_cache = Mock()

    assert service.cancel_conversation(conversation_id) is True
    service._clear_pandoc_cache.assert_called_once_with(conversation_id)
    task.cancel.assert_called_once_with()


def test_delete_conversation_clears_cache():
    service = AgentService.__new__(AgentService)
    conversation_id = "conversation-a"
    service._conversations = {
        conversation_id: Conversation(
            id=conversation_id,
            workspace_id="workspace",
            session_id="session",
            status=ConversationStatus.COMPLETED,
        )
    }
    service._clear_pandoc_cache = Mock()

    assert service.delete_conversation(conversation_id) is True
    service._clear_pandoc_cache.assert_called_once_with(conversation_id)
    assert conversation_id not in service._conversations
