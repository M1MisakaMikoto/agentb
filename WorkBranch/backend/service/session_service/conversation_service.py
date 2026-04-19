import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, Awaitable, List
from enum import Enum
from datetime import datetime, timezone

from core.logging import bind_ctx
from singleton import get_agent_service, get_conversation_dao, get_logging_runtime, get_message_queue, get_workspace_service, get_settings_service
from service.agent_service.agent_service import AgentService
from data.conversation_dao import ConversationDAO
from service.session_service.canonical import Message, SegmentType, MessageBuilder
from service.session_service.message_content import deserialize_parts, normalize_user_content, parts_to_plain_text, resolve_runtime_parts, serialize_parts


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
    handoff_metadata: Optional[Dict[str, Any]] = None


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
        self._workspace_service = get_workspace_service()
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

    def _is_plan_auto_approve_enabled(self) -> bool:
        settings = get_settings_service()
        try:
            return bool(settings.get("agent:plan_auto_approve"))
        except KeyError:
            return False

    async def _create_auto_approved_followup_conversation(
        self,
        conversation_id: str,
        *,
        final_reply: Optional[str] = None,
        session_id: Optional[int | str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self._is_plan_auto_approve_enabled():
            return None

        saw_plan = False
        plan_prompt_present = False

        if final_reply is not None:
            saw_plan = bool(final_reply)
            plan_prompt_present = "如果你同意方案，请直接回复“可以”或“同意方案”" in final_reply
        else:
            conversation = await self.get_conversation(conversation_id)
            if not conversation:
                return None

            assistant_content = conversation.get("assistant_content")
            if not assistant_content:
                return None

            try:
                events = json.loads(assistant_content)
            except Exception:
                return None

            for event in events:
                event_type = event.get("type")
                if event_type in {"plan_start", "plan_delta", "plan_end"}:
                    saw_plan = True
                if event_type == SegmentType.TEXT_DELTA.value and "如果你同意方案，请直接回复“可以”或“同意方案”" in str(event.get("content", "")):
                    plan_prompt_present = True

        if not saw_plan or not plan_prompt_present:
            return None

        if session_id is None:
            persisted = await self._dao.get_conversation_by_id(conversation_id)
            if not persisted:
                return None
            session_id = persisted.session_id

        next_conversation_id = await self.create_conversation(
            session_id=int(session_id),
            user_content="可以",
            allow_existing_running=True,
        )

        handoff_metadata = {
            "event": "plan_auto_approved",
            "plan_status": "auto_approved",
            "approval_message": "可以",
            "next_conversation_id": next_conversation_id,
        }

        async with self._lock:
            conv_info = self._conversations.get(conversation_id)
            if conv_info:
                conv_info.handoff_metadata = handoff_metadata

        self._write_content_record(
            conversation_id,
            "system_event",
            {
                "event": "plan.auto_approved",
                "next_conversation_id": next_conversation_id,
            },
        )
        return handoff_metadata

    async def create_conversation(
        self,
        session_id: int,
        user_content: Any,
        allow_existing_running: bool = False,
    ) -> str:
        conversation_id = str(uuid.uuid4())
        
        session = await self._dao.get_session_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        existing_conversations = await self._dao.list_conversations_by_session(session_id)
        if not allow_existing_running and any(conv.state == ConversationState.RUNNING.value for conv in existing_conversations):
            raise RuntimeError(f"Session {session_id} already has a running conversation")

        workspace_id = session.workspace_id

        normalized_parts = normalize_user_content(user_content)
        await self._dao.create_conversation(
            conversation_id=conversation_id,
            session_id=session_id,
            user_content=serialize_parts(normalized_parts),
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
        user_message: Any,
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

        normalized_parts = normalize_user_content(user_message)
        await self._dao.update_conversation(
            conversation_id,
            user_content=serialize_parts(normalized_parts),
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
        workspace_dir = self._workspace_service.get_workspace_dir(conv_info.workspace_id)

        persisted_conv = await self._dao.get_conversation_by_id(conversation_id)
        user_message_parts = resolve_runtime_parts(
            deserialize_parts(persisted_conv.user_content) if persisted_conv else [],
            workspace_dir,
        )
        history_context = []
        for item in context if context else []:
            role = item.get("role", "user")
            parts = item.get("parts") if isinstance(item, dict) else item
            history_context.append({
                "role": role,
                "parts": resolve_runtime_parts(parts, workspace_dir),
                "content": item.get("content", "") if isinstance(item, dict) else "",
            })

        message_id = f"msg-{conversation_id}-{int(datetime.now().timestamp() * 1000)}"

        async with self._lock:
            conv_info.state = ConversationState.RUNNING
            await self._dao.update_conversation(conversation_id, state=ConversationState.RUNNING.value)

        mq = self._get_mq()
        await mq.start_consumer()
        subscriber = mq.subscribe(conv_info.conversation_id)

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
                conversation_id=conv_info.conversation_id,
                message=user_message_parts,
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
                        if messages:
                            break
                        try:
                            await asyncio.wait_for(task, timeout=5.0)
                        except asyncio.TimeoutError:
                            task.cancel()
                            raise RuntimeError("Agent task finished without emitting any stream messages")
                        raise RuntimeError("Agent task finished without emitting any stream messages")
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
            messages_json = json.dumps([msg.to_dict() for msg in messages])
            async with self._lock:
                conv_info.state = ConversationState.CANCELLED
                await self._dao.update_conversation(
                    conversation_id,
                    assistant_content=messages_json,
                    state=ConversationState.CANCELLED.value,
                )
            raise

        except Exception as e:
            error_msg = str(e)
            messages_json = json.dumps([msg.to_dict() for msg in messages])
            async with self._lock:
                conv_info.state = ConversationState.FAILED
                conv_info.error = error_msg
                await self._dao.update_conversation(
                    conversation_id,
                    assistant_content=messages_json,
                    state=ConversationState.FAILED.value,
                    error=error_msg,
                )
            raise

        finally:
            mq.unsubscribe(conv_info.conversation_id, subscriber)

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
        user_parts = deserialize_parts(conv.user_content)
        user_text = parts_to_plain_text(user_parts)
        return {
            "id": conv.id,
            "session_id": conv.session_id,
            "user_content": user_text,
            "user_content_parts": user_parts,
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
                "user_content": parts_to_plain_text(deserialize_parts(conv.user_content)),
                "user_content_parts": deserialize_parts(conv.user_content),
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
