from typing import List, Optional
from dataclasses import dataclass


@dataclass
class Session:
    id: int
    user_id: int
    title: str
    created_at: str
    updated_at: str


@dataclass
class Conversation:
    id: str
    session_id: int
    workspace_id: Optional[str]
    user_content: str
    assistant_content: Optional[str]
    thinking_content: Optional[str]
    state: str
    error: Optional[str]
    created_at: str
    updated_at: str


class ConversationDAO:
    """会话和对话数据访问对象。"""

    def __init__(self, db):
        self._db = db

    async def create_session(self, user_id: int, title: str = "新会话") -> int:
        sql = '''
            INSERT INTO sessions (user_id, title)
            VALUES (%s, %s)
        '''
        return await self._db.execute(sql, (user_id, title))

    async def delete_session(self, session_id: int) -> None:
        sql = 'DELETE FROM sessions WHERE id = %s'
        await self._db.execute(sql, (session_id,))

    async def get_session_by_id(self, session_id: int) -> Optional[Session]:
        sql = '''
            SELECT id, user_id, title, created_at, updated_at
            FROM sessions
            WHERE id = %s
        '''
        row = await self._db.fetch_one(sql, (session_id,))
        if row:
            return Session(**dict(row))
        return None

    async def list_sessions_by_user(self, user_id: int) -> List[Session]:
        sql = '''
            SELECT id, user_id, title, created_at, updated_at
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
        workspace_id: Optional[str] = None,
    ) -> None:
        sql = '''
            INSERT INTO conversations (id, session_id, workspace_id, user_content, state)
            VALUES (%s, %s, %s, %s, 'pending')
        '''
        await self._db.execute(sql, (conversation_id, session_id, workspace_id, user_content))

    async def update_conversation(
        self,
        conversation_id: str,
        *,
        assistant_content: Optional[str] = None,
        thinking_content: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        updates = []
        params = []

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

        if not updates:
            return

        params.append(conversation_id)
        sql = f"UPDATE conversations SET {', '.join(updates)} WHERE id = %s"
        await self._db.execute(sql, tuple(params))

    async def get_conversation_by_id(self, conversation_id: str) -> Optional[Conversation]:
        sql = '''
            SELECT id, session_id, workspace_id, user_content, assistant_content,
                   thinking_content, state, error, created_at, updated_at
            FROM conversations
            WHERE id = %s
        '''
        row = await self._db.fetch_one(sql, (conversation_id,))
        if row:
            return Conversation(**dict(row))
        return None

    async def list_conversations_by_session(self, session_id: int) -> List[Conversation]:
        sql = '''
            SELECT id, session_id, workspace_id, user_content, assistant_content,
                   thinking_content, state, error, created_at, updated_at
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
            context.append({"role": "user", "content": row_dict["user_content"]})
            if row_dict["assistant_content"]:
                context.append({"role": "assistant", "content": row_dict["assistant_content"]})
        
        return context
