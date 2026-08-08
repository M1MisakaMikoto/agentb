import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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
        transition_conversation_state=AsyncMock(return_value=True),
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


def test_agent_task_callback_preserves_awaiting_state():
    from service.agent_service import agent_service as module

    conversation_id = "conversation-awaiting"
    service = object.__new__(module.AgentService)
    service._conversations = {
        conversation_id: SimpleNamespace(result=None, status=module.ConversationStatus.RUNNING)
    }
    task = Mock()
    task.result.return_value = {"status": "awaiting_user_input"}

    service._on_task_complete(conversation_id, task)

    assert (
        service._conversations[conversation_id].status
        == module.ConversationStatus.AWAITING_USER_INPUT
    )


def test_send_message_persists_awaiting_user_input_state():
    from service.session_service import conversation_service as module
    from service.session_service.canonical import MessageBuilder, SegmentType

    conversation_id = "conversation-awaiting"
    workspace_id = "workspace-awaiting"
    persisted = SimpleNamespace(
        id=conversation_id,
        session_id=7,
        user_content=serialize_parts([{"type": "text", "text": "需要批准"}]),
        state="pending",
    )
    dao = SimpleNamespace(
        get_session_context=AsyncMock(return_value=[]),
        get_conversation_by_id=AsyncMock(return_value=persisted),
        update_conversation=AsyncMock(),
        transition_conversation_state=AsyncMock(return_value=True),
    )
    workspace_service = SimpleNamespace(
        get_workspace_dir=Mock(return_value=None),
        consume_unnotified_files=Mock(return_value=[]),
    )
    queue = asyncio.Queue()

    class FakeAgent:
        async def send_message(self, **_kwargs):
            async def run():
                await queue.put((
                    MessageBuilder.build(
                        role="assistant",
                        message_id="message-awaiting",
                        conversation_id=conversation_id,
                        session_id="7",
                        workspace_id=workspace_id,
                        msg_type=SegmentType.USER_INPUT_REQUEST,
                        content='{"question":"批准？"}',
                    ),
                    1,
                ))
                return {"status": "awaiting_user_input"}

            return asyncio.create_task(run())

    class FakeMQ:
        async def start_consumer(self):
            return None

        def subscribe(self, _conversation_id):
            return queue

        def unsubscribe(self, _conversation_id, _subscriber):
            return None

    class ActiveSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    runtime = SimpleNamespace(
        instance_id="instance-test",
        claim_session=AsyncMock(return_value=SimpleNamespace(acquired=False)),
        active_session=Mock(return_value=ActiveSession()),
    )
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

    with patch.object(module, "get_runtime_state", return_value=runtime):
        result = asyncio.run(service.send_message(conversation_id))

    assert result["state"] == "awaiting_user_input"
    assert service._conversations[conversation_id].state == module.ConversationState.AWAITING_USER_INPUT
    states = [call.args[2] for call in dao.transition_conversation_state.await_args_list]
    assert states == ["running", "awaiting_user_input"]


def test_send_message_rejects_awaiting_user_input():
    from service.session_service import conversation_service as module

    conversation_id = "conversation-awaiting-reject"
    service = object.__new__(module.ConversationService)
    service._dao = SimpleNamespace(get_session_context=AsyncMock(return_value=[]))
    service._workspace_service = SimpleNamespace(
        get_workspace_dir=Mock(return_value=None),
        consume_unnotified_files=Mock(return_value=[]),
    )
    service._agent = SimpleNamespace(send_message=AsyncMock())
    service._mq = SimpleNamespace()
    service._conversations = {
        conversation_id: SimpleNamespace(
            conversation_id=conversation_id,
            session_id=7,
            workspace_id="ws",
            state=module.ConversationState.AWAITING_USER_INPUT,
        )
    }
    service._lock = asyncio.Lock()

    try:
        asyncio.run(service.send_message(conversation_id))
    except RuntimeError as exc:
        assert "awaiting_user_input" in str(exc)
    else:
        raise AssertionError("awaiting 状态应拒绝 send_message")


def test_create_conversation_rejects_awaiting_session():
    from service.session_service import conversation_service as module

    session_id = 7
    dao = SimpleNamespace(
        get_session_by_id=AsyncMock(
            return_value=SimpleNamespace(id=session_id, user_id=1, workspace_id="ws")
        ),
        fail_stale_running_conversations=AsyncMock(return_value=0),
        fail_expired_awaiting_conversations=AsyncMock(return_value=0),
        get_conversation_by_idempotency_key=AsyncMock(return_value=None),
        list_conversations_by_session=AsyncMock(
            return_value=[SimpleNamespace(state="awaiting_user_input")]
        ),
        create_conversation=AsyncMock(return_value=True),
    )
    runtime = SimpleNamespace(
        instance_id="instance-test",
        claim_session=AsyncMock(return_value=SimpleNamespace(acquired=True)),
    )
    service = object.__new__(module.ConversationService)
    service._dao = dao
    service._agent = SimpleNamespace(register_conversation=AsyncMock())
    service._mq = SimpleNamespace(register_stream=Mock())
    service._lock = asyncio.Lock()
    service._conversations = {}

    with patch.object(module, "get_runtime_state", return_value=runtime):
        try:
            asyncio.run(service.create_conversation(session_id, "新消息"))
        except RuntimeError as exc:
            assert "awaiting_user_input" in str(exc)
        else:
            raise AssertionError("awaiting 会话应拒绝创建新对话")
