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
from service.runtime import get_runtime_state


def _build_workspace_file_index_text(workspace_id: str, files: List[Dict[str, Any]]) -> str:
    index = {
        "workspace_files": [
            {
                "workspace_id": workspace_id,
                "name": file_info["name"],
                "relative_path": file_info["relative_path"],
                "size": file_info["size"],
            }
            for file_info in files
        ]
    }
    return f"`{json.dumps(index, ensure_ascii=False, separators=(',', ':'))}`"


class ConversationState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_USER_INPUT = "awaiting_user_input"
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
        execution_mode = None,
        session_id: Optional[int | str] = None,
    ) -> Optional[Dict[str, Any]]:
        mode_str = str(execution_mode).split(".")[-1] if execution_mode else None
        if mode_str != "PLAN":
            return None

        if not self._is_plan_auto_approve_enabled():
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
        idempotency_key: Optional[str] = None,
    ) -> str:
        conversation_id = str(uuid.uuid4())
        
        session = await self._dao.get_session_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        runtime = get_runtime_state()
        claim = await runtime.claim_session(session_id)
        if claim.acquired:
            await self._dao.fail_stale_running_conversations(
                session_id, runtime.instance_id
            )

        if idempotency_key:
            existing = await self._dao.get_conversation_by_idempotency_key(
                session_id, idempotency_key
            )
            if existing:
                return existing.id

        existing_conversations = await self._dao.list_conversations_by_session(session_id)
        if not allow_existing_running and any(conv.state == ConversationState.RUNNING.value for conv in existing_conversations):
            raise RuntimeError(f"Session {session_id} already has a running conversation")

        workspace_id = session.workspace_id

        normalized_parts = normalize_user_content(user_content)
        created = await self._dao.create_conversation(
            conversation_id=conversation_id,
            session_id=session_id,
            user_content=serialize_parts(normalized_parts),
            idempotency_key=idempotency_key,
        )
        if not created and idempotency_key:
            existing = await self._dao.get_conversation_by_idempotency_key(
                session_id, idempotency_key
            )
            if existing:
                return existing.id
            raise RuntimeError("Idempotent conversation creation could not be resolved")

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

        mq = self._get_mq()
        mq.register_stream(
            conversation_id=conversation_id,
            session_id=str(session_id),
            workspace_id=workspace_id
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

        await get_runtime_state().claim_session(conv_info.session_id)
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
        silent_mode: bool = False,  # 静默模式：过滤流式delta事件
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

        runtime = get_runtime_state()
        claim = await runtime.claim_session(conv_info.session_id)
        if claim.acquired:
            await self._dao.fail_stale_running_conversations(
                conv_info.session_id, runtime.instance_id
            )

        context = await self._dao.get_session_context(
            conv_info.session_id, conversation_id
        )
        workspace_dir = self._workspace_service.get_workspace_dir(conv_info.workspace_id)

        persisted_conv = await self._dao.get_conversation_by_id(conversation_id)
        assert persisted_conv is not None, f"Conversation {conversation_id} not found"
        persisted_user_parts = deserialize_parts(persisted_conv.user_content)
        unnotified = self._workspace_service.consume_unnotified_files(conv_info.session_id)
        if unnotified:
            file_index_part = {
                "type": "text",
                "text": _build_workspace_file_index_text(conv_info.workspace_id, unnotified),
            }
            persisted_user_parts = [file_index_part, *persisted_user_parts]
            await self._dao.update_conversation(
                conversation_id,
                user_content=serialize_parts(persisted_user_parts),
            )
        user_message_parts = resolve_runtime_parts(persisted_user_parts, workspace_dir)
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

        mq = self._get_mq()
        await mq.start_consumer()

        # 如果是静默模式，更新stream_state
        if silent_mode:
            mq.register_stream(
                conversation_id=conv_info.conversation_id,
                session_id=conv_info.session_id,
                workspace_id=conv_info.workspace_id,
                silent_mode=True
            )

        subscriber = mq.subscribe(conv_info.conversation_id)

        try:
            transitioned = await self._dao.transition_conversation_state(
                conversation_id,
                [ConversationState.PENDING.value],
                ConversationState.RUNNING.value,
                owner_instance_id=runtime.instance_id,
            )
        except Exception as exc:
            mq.unsubscribe(conv_info.conversation_id, subscriber)
            if "Duplicate" in str(exc):
                raise RuntimeError(
                    f"Session {conv_info.session_id} already has a running conversation"
                ) from exc
            raise
        if not transitioned:
            mq.unsubscribe(conv_info.conversation_id, subscriber)
            raise RuntimeError(
                f"Conversation {conversation_id} is not pending and cannot be started"
            )

        async with self._lock:
            conv_info.state = ConversationState.RUNNING

        messages: List[Message] = []
        done_received = False
        terminal_error: Optional[str] = None
        active_session = runtime.active_session(conv_info.session_id)
        await active_session.__aenter__()

        async def collect_and_forward(message: Message):
            nonlocal done_received, terminal_error
            
            messages.append(message)
            if message.type in {SegmentType.DONE, SegmentType.ERROR, SegmentType.CANCELLED}:
                done_received = True
            if message.type == SegmentType.ERROR:
                terminal_error = message.content or "Agent execution failed"
            
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
                    message, seq = await asyncio.wait_for(subscriber.get(), timeout=1.0)
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
                        # 竞态保护：任务已完成但已发布的消息可能仍在订阅队列中
                        # （例如 blocked 批量发布 chat_start/delta/end/done 的场景）。
                        # 给订阅者一个短暂的送达窗口，避免误判为“无消息完成”。
                        try:
                            message, seq = await asyncio.wait_for(subscriber.get(), timeout=2.0)
                            await collect_and_forward(message)
                            continue
                        except asyncio.TimeoutError:
                            pass
                        raise RuntimeError("Agent task finished without emitting any stream messages")
                    continue

            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    task.cancel()

            if terminal_error:
                raise RuntimeError(terminal_error)

            task_result = task.result()
            messages_json = json.dumps([msg.to_dict() for msg in messages])

            if (
                isinstance(task_result, dict)
                and task_result.get("status") == "awaiting_user_input"
            ):
                async with self._lock:
                    conv_info.state = ConversationState.AWAITING_USER_INPUT
                    transitioned = await self._dao.transition_conversation_state(
                        conversation_id,
                        [ConversationState.RUNNING.value],
                        ConversationState.AWAITING_USER_INPUT.value,
                        assistant_content=messages_json,
                    )
                assert transitioned, "awaiting_user_input 状态持久化失败"
                return {
                    "conversation_id": conversation_id,
                    "state": ConversationState.AWAITING_USER_INPUT.value,
                }

            async with self._lock:
                conv_info.state = ConversationState.COMPLETED
                await self._dao.transition_conversation_state(
                    conversation_id,
                    [ConversationState.RUNNING.value],
                    ConversationState.COMPLETED.value,
                    assistant_content=messages_json,
                )

            return {
                "conversation_id": conversation_id,
                "state": ConversationState.COMPLETED.value,
            }

        except asyncio.CancelledError:
            messages_json = json.dumps([msg.to_dict() for msg in messages])
            async with self._lock:
                conv_info.state = ConversationState.CANCELLED
                await self._dao.transition_conversation_state(
                    conversation_id,
                    [ConversationState.RUNNING.value],
                    ConversationState.CANCELLED.value,
                    assistant_content=messages_json,
                )
            raise

        except Exception as e:
            error_msg = str(e)
            messages_json = json.dumps([msg.to_dict() for msg in messages])
            async with self._lock:
                conv_info.state = ConversationState.FAILED
                conv_info.error = error_msg
                await self._dao.transition_conversation_state(
                    conversation_id,
                    [ConversationState.RUNNING.value],
                    ConversationState.FAILED.value,
                    assistant_content=messages_json,
                    error=error_msg,
                )
            raise

        finally:
            mq.unsubscribe(conv_info.conversation_id, subscriber)
            await active_session.__aexit__(None, None, None)

    async def cancel_conversation(self, conversation_id: str) -> None:
        persisted = await self._dao.get_conversation_by_id(conversation_id)
        if not persisted:
            raise ValueError(f"Conversation {conversation_id} not found")
        await get_runtime_state().claim_session(persisted.session_id)
        task_to_cancel = None
        async with self._lock:
            conv_info = self._conversations.get(conversation_id)
            if conv_info and conv_info.task and not conv_info.task.done():
                task_to_cancel = conv_info.task
            if conv_info:
                conv_info.state = ConversationState.CANCELLED
        transitioned = await self._dao.transition_conversation_state(
            conversation_id,
            [ConversationState.RUNNING.value, ConversationState.PENDING.value],
            ConversationState.CANCELLED.value,
        )
        if transitioned:
            session = await self._dao.get_session_by_id(persisted.session_id)
            self._get_mq().publish_sync(
                Message(
                    role="assistant",
                    message_id=f"cancel-{conversation_id}",
                    conversation_id=conversation_id,
                    session_id=str(persisted.session_id),
                    workspace_id=session.workspace_id if session else "",
                    type=SegmentType.CANCELLED,
                    content="cancelled",
                    metadata={"reason": "cancelled_by_user"},
                )
            )
        if task_to_cancel is not None:
            task_to_cancel.cancel()

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
        persisted = await self._dao.get_conversation_by_id(conversation_id)
        if not persisted:
            return
        await get_runtime_state().claim_session(persisted.session_id)
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

        await get_runtime_state().claim_session(conv.session_id)

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
