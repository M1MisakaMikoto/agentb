from typing import List
import uuid
from singleton import get_user_info_dao, get_conversation_dao, get_workspace_service
from data.user_info_dao import UserInfoDAO
from data.conversation_dao import ConversationDAO, Session


class SessionHistory:
    """会话历史服务层：管理当前用户的会话列表。"""

    def __init__(self):
        self._user_dao: UserInfoDAO = get_user_info_dao()
        self._conv_dao: ConversationDAO = get_conversation_dao()

    def _get_current_user_id(self) -> int:
        """获取当前用户ID（内部方法）。"""
        user = self._user_dao.get_or_create_default_user()
        return user.id

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
