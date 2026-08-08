from typing import List, Optional
from dataclasses import dataclass
import json

from service.session_service.message_content import build_user_message, deserialize_parts, get_message_text, serialize_parts, try_deserialize_parts


@dataclass
class Session:
    id: int
    user_id: int
    title: str
    workspace_id: str
    created_at: str
    updated_at: str


@dataclass
class Conversation:
    id: str
    session_id: int
    user_content: str
    assistant_content: Optional[str]
    thinking_content: Optional[str]
    state: str
    error: Optional[str]
    owner_instance_id: Optional[str]
    idempotency_key: Optional[str]
    created_at: str
    updated_at: str


class ConversationDAO:
    """会话和对话数据访问对象。"""

    def __init__(self, db):
        self._db = db

    async def create_session(self, user_id: int, title: str = "新会话", workspace_id: str = None) -> int:
        sql = '''
            INSERT INTO sessions (user_id, title, workspace_id)
            VALUES (%s, %s, %s)
        '''
        return await self._db.execute(sql, (user_id, title, workspace_id))

    async def delete_session(self, session_id: int) -> None:
        sql = 'DELETE FROM sessions WHERE id = %s'
        await self._db.execute(sql, (session_id,))

    async def get_session_by_id(self, session_id: int) -> Optional[Session]:
        sql = '''
            SELECT id, user_id, title, workspace_id, created_at, updated_at
            FROM sessions
            WHERE id = %s
        '''
        row = await self._db.fetch_one(sql, (session_id,))
        if row:
            return Session(**dict(row))
        return None

    async def update_session_title(self, session_id: int, title: str) -> None:
        sql = '''
            UPDATE sessions
            SET title = %s
            WHERE id = %s
        '''
        await self._db.execute(sql, (title, session_id))

    async def list_sessions_by_user(self, user_id: int) -> List[Session]:
        sql = '''
            SELECT id, user_id, title, workspace_id, created_at, updated_at
            FROM sessions
            WHERE user_id = %s
            ORDER BY updated_at DESC
        '''
        rows = await self._db.fetch_all(sql, (user_id,))
        return [Session(**dict(row)) for row in rows]

    async def create_conversation(
        self,
        conversation_id: str,
        session_id: int,
        user_content: str,
        idempotency_key: Optional[str] = None,
    ) -> bool:
        if not isinstance(user_content, str):
            user_content = serialize_parts(user_content)
        sql = '''
            INSERT INTO conversations
                (id, session_id, user_content, state, idempotency_key)
            VALUES (%s, %s, %s, 'pending', %s)
        '''
        try:
            await self._db.execute(
                sql, (conversation_id, session_id, user_content, idempotency_key)
            )
            return True
        except Exception as exc:
            if idempotency_key and "Duplicate" in str(exc):
                return False
            raise

    async def get_conversation_by_idempotency_key(
        self, session_id: int, idempotency_key: str
    ) -> Optional[Conversation]:
        sql = '''
            SELECT id, session_id, user_content, assistant_content,
                   thinking_content, state, error, owner_instance_id,
                   idempotency_key, created_at, updated_at
            FROM conversations
            WHERE session_id = %s AND idempotency_key = %s
        '''
        row = await self._db.fetch_one(sql, (session_id, idempotency_key))
        return Conversation(**dict(row)) if row else None

    async def update_conversation(
        self,
        conversation_id: str,
        *,
        user_content: Optional[str] = None,
        assistant_content: Optional[str] = None,
        thinking_content: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
        owner_instance_id: Optional[str] = None,
    ) -> None:
        updates = []
        params = []

        if user_content is not None:
            if not isinstance(user_content, str):
                user_content = serialize_parts(user_content)
            updates.append('user_content = %s')
            params.append(user_content)
        if assistant_content is not None:
            updates.append('assistant_content = %s')
            params.append(assistant_content)
        if thinking_content is not None:
            updates.append('thinking_content = %s')
            params.append(thinking_content)
        if state is not None:
            updates.append('state = %s')
            params.append(state)
        if error is not None:
            updates.append('error = %s')
            params.append(error)
        if owner_instance_id is not None:
            updates.append('owner_instance_id = %s')
            params.append(owner_instance_id)

        if not updates:
            return

        params.append(conversation_id)
        sql = f"UPDATE conversations SET {', '.join(updates)} WHERE id = %s"
        await self._db.execute(sql, tuple(params))

    async def transition_conversation_state(
        self,
        conversation_id: str,
        expected_states: List[str],
        new_state: str,
        *,
        owner_instance_id: Optional[str] = None,
        assistant_content: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        if not expected_states:
            return False
        updates = ["state = %s"]
        params: list = [new_state]
        if owner_instance_id is not None:
            updates.append("owner_instance_id = %s")
            params.append(owner_instance_id)
        if assistant_content is not None:
            updates.append("assistant_content = %s")
            params.append(assistant_content)
        if error is not None:
            updates.append("error = %s")
            params.append(error)
        placeholders = ", ".join(["%s"] * len(expected_states))
        params.extend([conversation_id, *expected_states])
        sql = (
            f"UPDATE conversations SET {', '.join(updates)} "
            f"WHERE id = %s AND state IN ({placeholders})"
        )
        return await self._db.execute_affected(sql, tuple(params)) == 1

    async def fail_stale_running_conversations(
        self, session_id: int, owner_instance_id: str
    ) -> int:
        sql = '''
            UPDATE conversations
            SET state = 'failed', error = %s
            WHERE session_id = %s AND state = 'running'
              AND (owner_instance_id IS NULL OR owner_instance_id <> %s)
        '''
        return await self._db.execute_affected(
            sql,
            (
                "Previous owner lease expired before the task completed",
                session_id,
                owner_instance_id,
            ),
        )

    async def fail_expired_awaiting_conversations(
        self, session_id: int, timeout_seconds: int
    ) -> int:
        """将等待用户输入超过 timeout_seconds 的对话标记为 failed。

        返回受影响行数；用于惰性超时清理（awaiting 期间无后台任务，
        在创建新对话/恢复对话入口检查）。
        """
        sql = '''
            UPDATE conversations
            SET state = 'failed', error = %s
            WHERE session_id = %s AND state = 'awaiting_user_input'
              AND updated_at < (NOW() - INTERVAL %s SECOND)
        '''
        return await self._db.execute_affected(
            sql,
            ("等待用户输入超时，请创建新对话", session_id, timeout_seconds),
        )

    async def get_conversation_by_id(self, conversation_id: str) -> Optional[Conversation]:
        sql = '''
            SELECT id, session_id, user_content, assistant_content,
                   thinking_content, state, error, owner_instance_id,
                   idempotency_key, created_at, updated_at
            FROM conversations
            WHERE id = %s
        '''
        row = await self._db.fetch_one(sql, (conversation_id,))
        if row:
            return Conversation(**dict(row))
        return None

    async def list_conversations_by_session(self, session_id: int) -> List[Conversation]:
        sql = '''
            SELECT id, session_id, user_content, assistant_content,
                   thinking_content, state, error, owner_instance_id,
                   idempotency_key, created_at, updated_at
            FROM conversations
            WHERE session_id = %s
            ORDER BY created_at ASC
        '''
        rows = await self._db.fetch_all(sql, (session_id,))
        return [Conversation(**dict(row)) for row in rows]

    async def delete_conversation(self, conversation_id: str) -> None:
        sql = 'DELETE FROM conversations WHERE id = %s'
        await self._db.execute(sql, (conversation_id,))

    async def get_session_context(
        self,
        session_id: int,
        before_conversation_id: Optional[str] = None
    ) -> List[dict]:
        """
        获取 Session 内指定 Conversation 之前的所有历史对话。
        返回格式：[{"role": "user/assistant", "content": "..."}]
        """
        if before_conversation_id:
            sql = '''
                SELECT id, user_content, assistant_content, created_at
                FROM conversations
                WHERE session_id = %s AND created_at < (
                    SELECT created_at FROM conversations WHERE id = %s
                )
                ORDER BY created_at ASC
            '''
            rows = await self._db.fetch_all(sql, (session_id, before_conversation_id))
        else:
            sql = '''
                SELECT id, user_content, assistant_content, created_at
                FROM conversations
                WHERE session_id = %s
                ORDER BY created_at ASC
            '''
            rows = await self._db.fetch_all(sql, (session_id,))

        context = []
        for row in rows:
            row_dict = dict(row)
            user_parts = deserialize_parts(row_dict["user_content"])
            context.append(build_user_message("user", user_parts))
            if row_dict["assistant_content"]:
                assistant_content = row_dict["assistant_content"]
                try:
                    events = json.loads(assistant_content)
                    parts = []
                    for event in events:
                        event_type = event.get("type")
                        if event_type in {"text_delta", "chat_delta", "thinking_delta"}:
                            parts.append(event.get("content", ""))
                        elif event_type == "thinking_end":
                            metadata = event.get("metadata") or {}
                            if metadata.get("result"):
                                parts.append(metadata["result"])
                    assistant_content = "".join(parts) if parts else assistant_content
                except Exception:
                    pass
                context.append({
                    "role": "assistant",
                    "parts": [{"type": "text", "text": assistant_content}],
                    "content": assistant_content,
                })

        return context

    async def delete_conversations_after(self, conversation_id: str) -> int:
        """删除指定对话之后的所有对话（按创建时间）
        
        Args:
            conversation_id: 对话ID
        
        Returns:
            删除的对话数量
        """
        conv = await self.get_conversation_by_id(conversation_id)
        if not conv:
            return 0
        
        sql = '''
            DELETE FROM conversations
            WHERE session_id = %s
            AND created_at > %s
        '''
        return await self._db.execute(sql, (conv.session_id, conv.created_at))
