import hashlib
import secrets
from typing import Dict, Any, Optional

from singleton import get_user_info_dao
from data.user_info_dao import UserInfoDAO, User


def hash_password(password: str) -> str:
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()


def generate_token() -> str:
    """生成 Session Token"""
    return secrets.token_hex(32)


class UserService:
    """用户服务层：管理用户信息和认证。"""

    def __init__(self):
        self._dao: UserInfoDAO = get_user_info_dao()

    async def register(self, name: str, password: str) -> Dict[str, Any]:
        """用户注册"""
        existing = await self._dao.get_user_by_name(name)
        if existing:
            raise ValueError("用户名已存在")
        
        password_hash = hash_password(password)
        user_id = await self._dao.create_user(name, password_hash)
        
        return {"id": user_id, "name": name}

    async def login(self, name: str, password: str) -> Dict[str, Any]:
        """用户登录"""
        user = await self._dao.get_user_by_name(name)
        if not user:
            raise ValueError("用户名或密码错误")
        
        if user.password_hash != hash_password(password):
            raise ValueError("用户名或密码错误")
        
        token = generate_token()
        await self._dao.update_session_token(user.id, token)
        
        return {"id": user.id, "name": user.name, "token": token}

    async def logout(self, user_id: int) -> None:
        """用户登出"""
        await self._dao.update_session_token(user_id, None)

    async def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """验证 Token"""
        user = await self._dao.get_user_by_token(token)
        if not user:
            return None
        return {"id": user.id, "name": user.name}

    def get_current_user(self) -> User:
        """获取当前本地用户（兼容旧代码）"""
        return self._dao.get_or_create_default_user()

    def update_user_name(self, new_name: str) -> User:
        """更新当前用户的名称（兼容旧代码）"""
        user = self.get_current_user()
        self._dao.update_user_name(user.id, new_name)
        return self._dao.get_user_by_id(user.id)
