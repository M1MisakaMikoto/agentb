from typing import List, Optional
from dataclasses import dataclass

from data.conversation_dao import Session


@dataclass
class User:
    id: int
    name: str
    password_hash: str
    session_token: Optional[str]
    created_at: str
    updated_at: str


class UserInfoDAO:
    """用户信息数据访问对象。"""

    def __init__(self, db):
        self._db = db

    async def create_user(self, name: str, password_hash: str = "") -> int:
        """创建新用户，返回用户ID。"""
        sql = 'INSERT INTO users (name, password_hash) VALUES (%s, %s)'
        return await self._db.execute(sql, (name, password_hash))

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """根据ID获取用户。"""
        sql = 'SELECT id, name, password_hash, session_token, created_at, updated_at FROM users WHERE id = %s'
        row = await self._db.fetch_one(sql, (user_id,))
        if row:
            return User(**dict(row))
        return None

    async def get_user_by_name(self, name: str) -> Optional[User]:
        """根据用户名获取用户。"""
        sql = 'SELECT id, name, password_hash, session_token, created_at, updated_at FROM users WHERE name = %s'
        row = await self._db.fetch_one(sql, (name,))
        if row:
            return User(**dict(row))
        return None

    async def get_user_by_token(self, token: str) -> Optional[User]:
        """根据 session token 获取用户。"""
        sql = 'SELECT id, name, password_hash, session_token, created_at, updated_at FROM users WHERE session_token = %s'
        row = await self._db.fetch_one(sql, (token,))
        if row:
            return User(**dict(row))
        return None

    async def update_session_token(self, user_id: int, token: Optional[str]) -> None:
        """更新用户的 session token。"""
        sql = 'UPDATE users SET session_token = %s WHERE id = %s'
        await self._db.execute(sql, (token, user_id))

    async def update_user_name(self, user_id: int, new_name: str) -> None:
        """更新用户名称。"""
        sql = 'UPDATE users SET name = %s WHERE id = %s'
        await self._db.execute(sql, (new_name, user_id))

    async def delete_user(self, user_id: int) -> None:
        """删除用户。"""
        sql = 'DELETE FROM users WHERE id = %s'
        await self._db.execute(sql, (user_id,))

    async def list_sessions(self, user_id: int) -> List[Session]:
        """获取用户的所有会话，按更新时间倒序排列。"""
        sql = '''
            SELECT id, user_id, title, created_at, updated_at
            FROM sessions
            WHERE user_id = %s
            ORDER BY updated_at DESC
        '''
        rows = await self._db.fetch_all(sql, (user_id,))
        return [Session(**dict(row)) for row in rows]
