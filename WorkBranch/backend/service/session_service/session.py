import asyncio
from typing import List, Optional, Dict, Any, Callable, Awaitable

from singleton import get_session_history, get_conversation_service, get_conversation_dao
from service.user_service.session_history import SessionHistory
from service.session_service.conversation_service import ConversationService
from data.conversation_dao import ConversationDAO, Session


class SessionService:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if SessionService._initialized:
            return
        SessionService._initialized = True

        self._session_history: SessionHistory = get_session_history()
        self._conversation_service: ConversationService = get_conversation_service()
        self._dao: ConversationDAO = get_conversation_dao()
        self._lock = asyncio.Lock()

    def create_session(self, title: str = "新会话") -> Session:
        """创建新会话"""
        return self._session_history.create_session(title)

    def delete_session(self, session_id: int) -> bool:
        """删除会话"""
        self._session_history.delete_session(session_id)
        return True

    def list_sessions(self) -> List[Session]:
        """列出所有会话"""
        return self._session_history.list_sessions()

    def get_session(self, session_id: int) -> Optional[Session]:
        """获取会话详情"""
        return self._session_history.get_session(session_id)

    async def create_conversation(
        self,
        session_id: int,
        user_content: Optional[str] = None,
        workspace_id: Optional[str] = None,
        parent_conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建对话
        
        Args:
            session_id: 会话ID
            user_content: 用户消息内容（可选，用于新的线性对话模型）
            workspace_id: 工作区ID（可选）
            parent_conversation_id: 父对话ID（已废弃，保留用于向后兼容）
        
        Returns:
            包含 conversation_id 和 session_id 的字典
        """
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        conversation_id = await self._conversation_service.create_conversation(
            session_id=session_id,
            user_content=user_content or "",
            workspace_id=workspace_id,
        )

        return {
            "conversation_id": conversation_id,
            "session_id": session_id,
        }

    async def send_message_to_conversation(
        self,
        conversation_id: str,
        message: Optional[str] = None,
        on_chunk: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        enable_context: bool = False,
    ) -> Dict[str, Any]:
        """发送消息到对话
        
        Args:
            conversation_id: 对话ID
            message: 用户消息（已废弃，线性对话模型中消息在创建时设置）
            on_chunk: 流式回调
            enable_context: 是否启用上下文（已废弃，线性对话模型始终使用 Session 历史）
        
        Returns:
            包含 conversation_id、session_id 和 message_id 的字典
        """
        conversation = await self._dao.get_conversation_by_id(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")

        result = await self._conversation_service.send_message(
            conversation_id=conversation_id,
            on_chunk=on_chunk,
        )

        return {
            "conversation_id": result["conversation_id"],
            "session_id": conversation.session_id,
            "message_id": f"msg-{conversation_id}",
        }

    async def cancel_conversation(self, conversation_id: str) -> bool:
        """取消对话"""
        await self._conversation_service.cancel_conversation(conversation_id)
        return True

    async def delete_conversation(self, conversation_id: str) -> bool:
        """删除对话"""
        await self._conversation_service.delete_conversation(conversation_id)
        return True

    async def list_conversations(self, session_id: int) -> List[Dict[str, Any]]:
        """列出会话内的所有对话"""
        return await self._conversation_service.list_conversations(session_id)

    async def get_conversation_detail(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """获取对话详情"""
        return await self._conversation_service.get_conversation(conversation_id)

    async def list_conversation_summaries(self, session_id: int) -> List[Dict[str, Any]]:
        """列出会话内的对话摘要（向后兼容方法）"""
        conversations = await self._conversation_service.list_conversations(session_id)
        return [
            {
                "conversation_id": conv["id"],
                "parent_conversation_id": None,
                "title": None,
                "state": conv["state"],
                "message_count": 1,
                "created_at": conv["created_at"],
                "updated_at": conv["updated_at"],
                "position_x": None,
                "position_y": None,
            }
            for conv in conversations
        ]

    async def update_conversation_positions(
        self,
        session_id: int,
        positions: List[Dict[str, Any]]
    ) -> None:
        """更新对话位置（已废弃，保留用于向后兼容）"""
        pass

    async def get_conversation_messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        """获取对话消息列表（向后兼容方法）
        
        在线性对话模型中，一个 Conversation 只有一轮对话，
        所以返回单个消息或空列表。
        """
        conversation = await self._dao.get_conversation_by_id(conversation_id)
        if not conversation:
            return []
        
        if not conversation.user_content:
            return []
        
        return [
            {
                "id": f"msg-{conversation_id}",
                "conversation_id": conversation_id,
                "session_id": conversation.session_id,
                "user_content": conversation.user_content,
                "assistant_content": conversation.assistant_content,
                "thinking_content": conversation.thinking_content,
                "status": "completed" if conversation.state == "completed" else conversation.state,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }
        ]

    async def get_context_info(self, conversation_id: str) -> Dict[str, Any]:
        """获取上下文信息（向后兼容方法）"""
        conversation = await self._dao.get_conversation_by_id(conversation_id)
        if not conversation:
            return {
                "conversation_id": conversation_id,
                "message_count": 0,
                "total_chars": 0,
                "estimated_tokens": 0,
            }
        
        context = await self._dao.get_session_context(
            conversation.session_id, conversation_id
        )
        
        total_chars = sum(len(msg.get("content", "")) for msg in context)
        estimated_tokens = total_chars // 4
        
        return {
            "conversation_id": conversation_id,
            "message_count": len(context),
            "total_chars": total_chars,
            "estimated_tokens": estimated_tokens,
        }

    async def end_conversation(self, conversation_id: str) -> int:
        """结束对话（向后兼容方法）
        
        在线性对话模型中，对话在发送消息后自动结束。
        """
        conversation = await self._dao.get_conversation_by_id(conversation_id)
        if not conversation:
            return 0
        return 1

    async def cascade_delete_conversation(self, conversation_id: str) -> bool:
        """级联删除对话（向后兼容方法）
        
        在线性对话模型中，没有子节点，所以等同于普通删除。
        """
        return await self.delete_conversation(conversation_id)
