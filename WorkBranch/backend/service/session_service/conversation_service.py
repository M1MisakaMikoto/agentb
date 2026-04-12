import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, Awaitable, List
from enum import Enum
from datetime import datetime, timezone

from core.logging import bind_ctx
from singleton import get_agent_service, get_conversation_dao, get_logging_runtime, get_message_queue
from service.agent_service.agent_service import AgentService
from data.conversation_dao import ConversationDAO
from service.session_service.canonical import Message, SegmentType


class ConversationState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ConversationInfo:
    conversation_id: str
    session_id: int
    workspace_id: str
    state: ConversationState = ConversationState.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    task: Optional[asyncio.Task] = None
    error: Optional[str] = None


class ConversationService:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if ConversationService._initialized:
            return
        ConversationService._initialized = True

        self._agent: AgentService = get_agent_service()
        self._dao: ConversationDAO = get_conversation_dao()
        self._mq = None
        self._runtime = None
        self._conversations: Dict[str, ConversationInfo] = {}
        self._lock = asyncio.Lock()

    def _get_mq(self):
        if self._mq is None:
            self._mq = get_message_queue()
        return self._mq

    def _get_logger(self):
        if self._runtime is None:
            self._runtime = get_logging_runtime()
        return self._runtime.get_logger("app")

    def _write_content_record(self, conversation_id: str, content_type: str, payload: Dict[str, Any]) -> None:
        if self._runtime is None:
            self._runtime = get_logging_runtime()
        self._runtime.write_conversation_content(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "conversation_id": conversation_id,
                "type": content_type,
                "payload": payload,
            }
        )

    async def create_conversation(
        self,
        session_id: int,
        user_content: str,
    ) -> str:
        conversation_id = str(uuid.uuid4())
        
        session = await self._dao.get_session_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        workspace_id = session.workspace_id

        await self._dao.create_conversation(
            conversation_id=conversation_id,
            session_id=session_id,
            user_content=user_content,
        )

        async with self._lock:
            self._conversations[conversation_id] = ConversationInfo(
                conversation_id=conversation_id,
                session_id=session_id,
                workspace_id=workspace_id,
                state=ConversationState.PENDING,
            )

        await self._agent.register_conversation(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            session_id=str(session_id),
        )

        self._write_content_record(
            conversation_id,
            "system_event",
            {
                "event": "conversation.created",
                "session_id": session_id,
                "workspace_id": workspace_id,
            },
        )

        return conversation_id

    async def prepare_message(
        self,
        conversation_id: str,
        user_message: str,
    ) -> Dict[str, Any]:
        """准备消息 - 更新用户消息内容但不执行 Agent
        
        Args:
            conversation_id: 对话ID
            user_message: 用户消息内容
            
        Returns:
            包含 conversation_id 和 message_id 的字典
        """
        async with self._lock:
            conv_info = self._conversations.get(conversation_id)
            if not conv_info:
                persisted = await self._dao.get_conversation_by_id(conversation_id)
                if not persisted:
                    raise ValueError(f"Conversation {conversation_id} not found")
                
                session = await self._dao.get_session_by_id(persisted.session_id)
                if not session:
                    raise ValueError(f"Session {persisted.session_id} not found")
                
                conv_info = ConversationInfo(
                    conversation_id=persisted.id,
                    session_id=persisted.session_id,
                    workspace_id=session.workspace_id,
                    state=ConversationState(persisted.state),
                )
                self._conversations[conversation_id] = conv_info

            if conv_info.state == ConversationState.RUNNING:
                raise RuntimeError(f"Conversation {conversation_id} is already running")

        await self._dao.update_conversation(
            conversation_id,
            user_content=user_message,
        )

        message_id = f"msg-{conversation_id}-{int(datetime.now().timestamp() * 1000)}"

        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "state": ConversationState.PENDING.value,
        }

    async def send_message(
        self,
        conversation_id: str,
        on_chunk: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        async with self._lock:
            conv_info = self._conversations.get(conversation_id)
            if not conv_info:
                persisted = await self._dao.get_conversation_by_id(conversation_id)
                if not persisted:
                    raise ValueError(f"Conversation {conversation_id} not found")
                
                session = await self._dao.get_session_by_id(persisted.session_id)
                if not session:
                    raise ValueError(f"Session {persisted.session_id} not found")
                
                conv_info = ConversationInfo(
                    conversation_id=persisted.id,
                    session_id=persisted.session_id,
                    workspace_id=session.workspace_id,
                    state=ConversationState(persisted.state),
                )
                self._conversations[conversation_id] = conv_info

            if conv_info.state == ConversationState.RUNNING:
                raise RuntimeError(f"Conversation {conversation_id} is already running")

        context = await self._dao.get_session_context(
            conv_info.session_id, conversation_id
        )

        persisted_conv = await self._dao.get_conversation_by_id(conversation_id)
        user_message = persisted_conv.user_content if persisted_conv else ""
        history_context = context if context else []

        message_id = f"msg-{conversation_id}-{int(datetime.now().timestamp() * 1000)}"

        async with self._lock:
            conv_info.state = ConversationState.RUNNING
            await self._dao.update_conversation(conversation_id, state=ConversationState.RUNNING.value)

        mq = self._get_mq()
        await mq.start_consumer()
        subscriber = mq.subscribe(conv_info.workspace_id)

        messages: List[Message] = []
        done_received = False

        async def collect_and_forward(message: Message):
            nonlocal done_received
            
            messages.append(message)
            if message.type == SegmentType.DONE:
                done_received = True
            
            if on_chunk:
                await on_chunk(message.to_dict())

        try:
            task = await self._agent.send_message(
                conversation_id=conv_info.workspace_id,
                message=user_message,
                message_id=message_id,
                stream_callback=None,
                parent_chain_messages=history_context,
                current_conversation_messages=[],
            )

            async with self._lock:
                conv_info.task = task

            while not done_received:
                try:
                    message = await asyncio.wait_for(subscriber.get(), timeout=1.0)
                    await collect_and_forward(message)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    continue

            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    task.cancel()

            messages_json = json.dumps([msg.to_dict() for msg in messages])

            async with self._lock:
                conv_info.state = ConversationState.COMPLETED
                await self._dao.update_conversation(
                    conversation_id,
                    assistant_content=messages_json,
                    state=ConversationState.COMPLETED.value,
                )

            return {
                "conversation_id": conversation_id,
                "state": ConversationState.COMPLETED.value,
            }

        except asyncio.CancelledError:
            async with self._lock:
                conv_info.state = ConversationState.CANCELLED
                await self._dao.update_conversation(
                    conversation_id,
                    state=ConversationState.CANCELLED.value,
                )
            raise

        except Exception as e:
            error_msg = str(e)
            async with self._lock:
                conv_info.state = ConversationState.FAILED
                conv_info.error = error_msg
                await self._dao.update_conversation(
                    conversation_id,
                    state=ConversationState.FAILED.value,
                    error=error_msg,
                )
            raise

        finally:
            mq.unsubscribe(conv_info.workspace_id, subscriber)

    async def cancel_conversation(self, conversation_id: str) -> None:
        async with self._lock:
            conv_info = self._conversations.get(conversation_id)
            if conv_info and conv_info.task and not conv_info.task.done():
                conv_info.task.cancel()
            if conv_info:
                conv_info.state = ConversationState.CANCELLED
                await self._dao.update_conversation(
                    conversation_id,
                    state=ConversationState.CANCELLED.value,
                )

    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        conv = await self._dao.get_conversation_by_id(conversation_id)
        if not conv:
            return None
        return {
            "id": conv.id,
            "session_id": conv.session_id,
            "user_content": conv.user_content,
            "assistant_content": conv.assistant_content,
            "thinking_content": conv.thinking_content,
            "state": conv.state,
            "error": conv.error,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        }

    async def list_conversations(self, session_id: int) -> List[Dict[str, Any]]:
        conversations = await self._dao.list_conversations_by_session(session_id)
        return [
            {
                "id": conv.id,
                "session_id": conv.session_id,
                "user_content": conv.user_content,
                "assistant_content": conv.assistant_content,
                "thinking_content": conv.thinking_content,
                "state": conv.state,
                "error": conv.error,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
            }
            for conv in conversations
        ]

    async def delete_conversation(self, conversation_id: str) -> None:
        async with self._lock:
            conv_info = self._conversations.get(conversation_id)
            if conv_info and conv_info.task and not conv_info.task.done():
                conv_info.task.cancel()

            if conversation_id in self._conversations:
                del self._conversations[conversation_id]

        await self._dao.delete_conversation(conversation_id)
        self._agent.delete_conversation(conversation_id)

    async def delete_conversations_after(self, conversation_id: str) -> int:
        """删除指定对话之后的所有对话
        
        Args:
            conversation_id: 对话ID
        
        Returns:
            删除的对话数量
        """
        conv = await self._dao.get_conversation_by_id(conversation_id)
        if not conv:
            return 0

        async with self._lock:
            to_delete = []
            for cid, info in self._conversations.items():
                if info.session_id == conv.session_id and info.created_at > conv.created_at:
                    if info.task and not info.task.done():
                        info.task.cancel()
                    to_delete.append(cid)
            
            for cid in to_delete:
                if cid in self._conversations:
                    del self._conversations[cid]
        
        deleted_count = await self._dao.delete_conversations_after(conversation_id)
        self._agent.delete_conversation(conversation_id)
        return deleted_count
