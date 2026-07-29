import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.session_service.message_content import (
    MessageContentError,
    serialize_parts,
)


def parse_index_text(value: str) -> dict:
    assert value.startswith("`") and value.endswith("`")
    return json.loads(value[1:-1])


def test_workspace_file_index_text_is_compact_parseable_json():
    from service.session_service.conversation_service import _build_workspace_file_index_text

    files = [
        {"name": "report.pdf", "relative_path": "reports/report.pdf", "size": 2048},
        {"name": "photo.jpg", "relative_path": "images/photo.jpg", "size": 512},
    ]

    marker = _build_workspace_file_index_text("workspace-1", files)

    assert ": " not in marker
    assert ", " not in marker
    assert parse_index_text(marker) == {
        "workspace_files": [
            {"workspace_id": "workspace-1", **file_info}
            for file_info in files
        ]
    }


def test_workspace_file_is_not_a_message_part_type():
    with pytest.raises(MessageContentError, match="不支持的 part 类型"):
        serialize_parts([{
            "type": "workspace_file",
            "workspace_id": "workspace-1",
            "name": "report.pdf",
            "relative_path": "report.pdf",
            "size": 1,
        }])


@pytest.mark.parametrize(
    "unnotified",
    [
        [],
        [
            {
                "name": "report.pdf",
                "relative_path": "reports/report.pdf",
                "size": 2048,
            },
            {
                "name": "photo.jpg",
                "relative_path": "images/photo.jpg",
                "size": 512,
            },
        ],
    ],
)
def test_send_message_persists_index_text_before_original_content(unnotified):
    from service.session_service import conversation_service as module

    conversation_id = "conversation-1"
    workspace_id = "workspace-1"
    original_parts = [{"type": "text", "text": "分析报告"}]
    persisted = SimpleNamespace(
        id=conversation_id,
        session_id=7,
        user_content=serialize_parts(original_parts),
        state="pending",
    )
    dao = SimpleNamespace(
        get_session_context=AsyncMock(return_value=[]),
        get_conversation_by_id=AsyncMock(return_value=persisted),
        update_conversation=AsyncMock(),
    )
    workspace_service = SimpleNamespace(
        get_workspace_dir=Mock(return_value=None),
        consume_unnotified_files=Mock(return_value=unnotified),
    )
    observed = {}

    class FakeAgent:
        async def send_message(self, **kwargs):
            observed["parts"] = kwargs["message"]
            raise RuntimeError("stop after observing agent input")

    class FakeMQ:
        async def start_consumer(self):
            return None

        def subscribe(self, _conversation_id):
            return object()

        def unsubscribe(self, _conversation_id, _subscriber):
            return None

    service = object.__new__(module.ConversationService)
    service._dao = dao
    service._workspace_service = workspace_service
    service._agent = FakeAgent()
    service._mq = FakeMQ()
    service._runtime = None
    service._lock = asyncio.Lock()
    service._conversations = {
        conversation_id: module.ConversationInfo(
            conversation_id=conversation_id,
            session_id=7,
            workspace_id=workspace_id,
        )
    }

    with pytest.raises(RuntimeError, match="stop after observing"):
        asyncio.run(service.send_message(conversation_id))

    user_content_updates = [
        call.kwargs["user_content"]
        for call in dao.update_conversation.await_args_list
        if "user_content" in call.kwargs
    ]
    assert len(user_content_updates) == (1 if unnotified else 0)
    stored_parts = (
        json.loads(user_content_updates[0])
        if user_content_updates
        else original_parts
    )
    assert stored_parts == observed["parts"]
    if not unnotified:
        assert stored_parts == original_parts
        return

    marker = stored_parts[0]
    assert marker["type"] == "text"
    assert parse_index_text(marker["text"]) == {
        "workspace_files": [
            {"workspace_id": workspace_id, **file_info}
            for file_info in unnotified
        ]
    }
    assert stored_parts[1:] == original_parts


def test_conversation_read_returns_index_at_start_of_user_content():
    from service.session_service import conversation_service as module

    files = [{"name": "report.pdf", "relative_path": "reports/report.pdf", "size": 2048}]
    marker = module._build_workspace_file_index_text("workspace-1", files)
    persisted = SimpleNamespace(
        id="conversation-1",
        session_id=7,
        user_content=serialize_parts([
            {"type": "text", "text": marker},
            {"type": "text", "text": "分析报告"},
        ]),
        assistant_content=None,
        thinking_content=None,
        state="failed",
        error="agent failed",
        created_at="2026-07-29T00:00:00",
        updated_at="2026-07-29T00:00:01",
    )
    service = object.__new__(module.ConversationService)
    service._dao = SimpleNamespace(
        get_conversation_by_id=AsyncMock(return_value=persisted),
    )

    result = asyncio.run(service.get_conversation(persisted.id))

    assert result["user_content"] == f"{marker} 分析报告"
    assert result["user_content_parts"] == [
        {"type": "text", "text": marker},
        {"type": "text", "text": "分析报告"},
    ]
