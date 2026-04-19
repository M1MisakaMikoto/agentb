import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from data.conversation_dao import Session
from service.user_service.session_history import SessionHistory


class FakeConversationDAO:
    def __init__(self, session=None, context=None):
        self.session = session
        self.context = list(context or [])
        self.updated_title = None

    async def get_session_by_id(self, session_id):
        return self.session

    async def get_session_context(self, session_id):
        return list(self.context)

    async def update_session_title(self, session_id, title):
        self.updated_title = title
        if self.session is not None:
            self.session = Session(
                id=self.session.id,
                user_id=self.session.user_id,
                title=title,
                workspace_id=self.session.workspace_id,
                created_at=self.session.created_at,
                updated_at=self.session.updated_at,
            )


class FakeUserDAO:
    def get_or_create_default_user(self):
        return SimpleNamespace(id=1)


class FakeLLMService:
    def __init__(self, title=None, structured_error=None, chat_response=None):
        self.title = title
        self.structured_error = structured_error
        self.chat_response = chat_response
        self.calls = []

    def structured_output(self, messages, schema, system_prompt=None, **kwargs):
        self.calls.append({
            "method": "structured_output",
            "messages": messages,
            "schema": schema,
            "system_prompt": system_prompt,
            "kwargs": kwargs,
        })
        if self.structured_error is not None:
            raise self.structured_error
        return schema(title=self.title)

    def chat(self, messages, system_prompt=None, **kwargs):
        self.calls.append({
            "method": "chat",
            "messages": messages,
            "system_prompt": system_prompt,
            "kwargs": kwargs,
        })
        return self.chat_response


def build_session(title="旧标题", user_id=1):
    return Session(
        id=1,
        user_id=user_id,
        title=title,
        workspace_id="ws-1",
        created_at="2026-04-19 00:00:00",
        updated_at="2026-04-19 00:00:00",
    )


@pytest.mark.asyncio
async def test_generate_session_title_overwrites_existing_title():
    dao = FakeConversationDAO(
        session=build_session(),
        context=[
            {"role": "user", "content": "帮我排查 FastAPI 登录接口 403 问题"},
            {"role": "assistant", "content": "先检查鉴权中间件和请求头。"},
        ],
    )
    llm = FakeLLMService('  "FastAPI 登录 403 排查"  ')

    with patch("service.user_service.session_history.get_user_info_dao", return_value=FakeUserDAO()), \
         patch("service.user_service.session_history.get_conversation_dao", return_value=dao), \
         patch("service.user_service.session_history.get_llm_service", return_value=llm):
        service = SessionHistory()
        updated = await service.generate_session_title_async(1, 1)

    assert updated.title == "FastAPI 登录 403 排查"
    assert dao.updated_title == "FastAPI 登录 403 排查"
    assert llm.calls


@pytest.mark.asyncio
async def test_generate_session_title_rejects_non_owner():
    dao = FakeConversationDAO(session=build_session(user_id=2), context=[{"role": "user", "content": "hello"}])
    llm = FakeLLMService("不会被用到")

    with patch("service.user_service.session_history.get_user_info_dao", return_value=FakeUserDAO()), \
         patch("service.user_service.session_history.get_conversation_dao", return_value=dao), \
         patch("service.user_service.session_history.get_llm_service", return_value=llm):
        service = SessionHistory()
        with pytest.raises(PermissionError, match="无权修改该会话标题"):
            await service.generate_session_title_async(1, 1)

    assert dao.updated_title is None
    assert not llm.calls


@pytest.mark.asyncio
async def test_generate_session_title_falls_back_to_chat_when_structured_output_returns_markdown_json():
    dao = FakeConversationDAO(
        session=build_session(),
        context=[
            {"role": "user", "content": "帮我总结这次关于 SQL 查询工具接入的问题定位"},
            {"role": "assistant", "content": "主要在 structured_output 返回格式不稳定。"},
        ],
    )
    llm = FakeLLMService(
        structured_error=ValueError("Invalid response from LLM: expected JSON"),
        chat_response='```json\n{"title": "SQL 查询工具输出格式排查"}\n```',
    )

    with patch("service.user_service.session_history.get_user_info_dao", return_value=FakeUserDAO()), \
         patch("service.user_service.session_history.get_conversation_dao", return_value=dao), \
         patch("service.user_service.session_history.get_llm_service", return_value=llm):
        service = SessionHistory()
        updated = await service.generate_session_title_async(1, 1)

    assert updated.title == "SQL 查询工具输出格式排查"
    assert dao.updated_title == "SQL 查询工具输出格式排查"
    assert [call["method"] for call in llm.calls] == ["structured_output", "chat"]


@pytest.mark.asyncio
async def test_generate_session_title_requires_usable_history():
    dao = FakeConversationDAO(
        session=build_session(),
        context=[
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": ""},
        ],
    )
    llm = FakeLLMService("不会被用到")

    with patch("service.user_service.session_history.get_user_info_dao", return_value=FakeUserDAO()), \
         patch("service.user_service.session_history.get_conversation_dao", return_value=dao), \
         patch("service.user_service.session_history.get_llm_service", return_value=llm):
        service = SessionHistory()
        with pytest.raises(RuntimeError, match="当前会话没有可用于生成标题的历史内容"):
            await service.generate_session_title_async(1, 1)

    assert dao.updated_title is None
    assert not llm.calls
