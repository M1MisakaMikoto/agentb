from typing import List
import re
import uuid

from pydantic import BaseModel

from singleton import get_user_info_dao, get_conversation_dao, get_workspace_service, get_llm_service
from data.user_info_dao import UserInfoDAO
from data.conversation_dao import ConversationDAO, Session


class SessionTitleResult(BaseModel):
    title: str


class SessionHistory:
    """会话历史服务层：管理当前用户的会话列表。"""

    TITLE_PROMPT = """你是一个擅长概括会话主题的助手。请根据给定的多轮对话，为整个 session 生成一个简短标题。\n\n你必须返回严格的 JSON，对应 schema 中的 `title` 字段。\n\n输出示例：\n```json\n{\"title\": \"FastAPI 登录 403 排查\"}\n```\n\n要求：\n1. 只概括主要主题，不要写解释\n2. 输出语言跟随对话主语言；若无法判断，默认中文\n3. 标题简短明确，避免泛化表述，如“新会话”“聊天记录”\n4. `title` 不要包含引号、句号、换行或前缀\n5. 只返回 schema 需要的 JSON 字段，不要添加额外字段"""

    def __init__(self):
        self._user_dao: UserInfoDAO = get_user_info_dao()
        self._conv_dao: ConversationDAO = get_conversation_dao()
        self._llm = get_llm_service()

    def _get_current_user_id(self) -> int:
        """获取当前用户ID（内部方法）。"""
        user = self._user_dao.get_or_create_default_user()
        return user.id

    @staticmethod
    def _normalize_title(title: str) -> str:
        normalized = re.sub(r"\s+", " ", title).strip().strip('"\'“”‘’')
        return normalized[:255].strip()

    def _build_title_messages(self, context: List[dict]) -> List[dict]:
        if len(context) <= 12:
            return context
        return context[:2] + context[-10:]

    def _generate_title(self, context: List[dict]) -> str:
        result = self._llm.structured_output(
            self._build_title_messages(context),
            SessionTitleResult,
            system_prompt=self.TITLE_PROMPT,
        )
        return self._normalize_title(result.title)

    def list_sessions(self) -> List[Session]:
        """
        获取当前用户的所有会话。
        按更新时间倒序排列。
        """
        user_id = self._get_current_user_id()
        return self._user_dao.list_sessions(user_id)

    def create_session(self, title: str) -> Session:
        """
        为当前用户创建新会话。
        返回创建的会话对象。
        """
        user_id = self._get_current_user_id()
        workspace_id = str(uuid.uuid4())[:8]
        session_id = self._conv_dao.create_session(user_id, title, workspace_id)
        
        workspace_service = get_workspace_service()
        workspace_service.register(workspace_id=workspace_id, session_id=str(session_id))
        
        return self._conv_dao.get_session_by_id(session_id)

    def delete_session(self, session_id: int) -> None:
        """
        删除指定会话。
        会级联删除会话下的所有节点。
        """
        self._conv_dao.delete_session(session_id)

    def get_session(self, session_id: int) -> Session:
        """
        根据ID获取会话详情。
        """
        return self._conv_dao.get_session_by_id(session_id)

    async def list_sessions_async(self, user_id: int) -> List[Session]:
        """异步获取用户的所有会话。"""
        return await self._conv_dao.list_sessions_by_user(user_id)

    async def create_session_async(self, user_id: int, title: str) -> Session:
        """异步创建会话。"""
        # 确保用户存在
        from singleton import get_user_info_dao
        user_dao = get_user_info_dao()
        await user_dao.get_or_create_user_by_id(user_id)
        
        workspace_id = str(uuid.uuid4())[:8]
        session_id = await self._conv_dao.create_session(user_id, title, workspace_id)
        
        workspace_service = get_workspace_service()
        workspace_service.register(workspace_id=workspace_id, session_id=str(session_id))
        
        return await self._conv_dao.get_session_by_id(session_id)

    async def delete_session_async(self, session_id: int) -> None:
        """异步删除会话。"""
        await self._conv_dao.delete_session(session_id)

    async def get_session_async(self, session_id: int) -> Session:
        """异步获取会话详情。"""
        return await self._conv_dao.get_session_by_id(session_id)

    async def generate_session_title_async(self, session_id: int, user_id: int) -> Session:
        """基于会话历史生成标题并覆盖原标题。"""
        session = await self._conv_dao.get_session_by_id(session_id)
        if not session:
            raise ValueError("会话不存在")
        if session.user_id != user_id:
            raise PermissionError("无权修改该会话标题")

        context = await self._conv_dao.get_session_context(session_id)
        usable_context = [item for item in context if (item.get("content") or "").strip()]
        if not usable_context:
            raise RuntimeError("当前会话没有可用于生成标题的历史内容")

        title = self._generate_title(usable_context)
        if not title:
            raise RuntimeError("生成的标题为空")

        await self._conv_dao.update_session_title(session_id, title)
        updated_session = await self._conv_dao.get_session_by_id(session_id)
        if not updated_session:
            raise RuntimeError("标题更新后未找到会话")
        return updated_session
