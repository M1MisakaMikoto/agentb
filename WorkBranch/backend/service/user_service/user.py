from typing import Dict, Any

from singleton import get_user_info_dao
from data.user_info_dao import UserInfoDAO, User


class UserService:
    """用户服务层：管理用户信息。"""

    def __init__(self):
        self._dao: UserInfoDAO = get_user_info_dao()

    async def get_or_create_user(self, user_id: int) -> Dict[str, Any]:
        """获取或创建用户。"""
        user = await self._dao.get_or_create_user_by_id(user_id)
        return {"id": user.id, "name": user.name}

    async def get_user_by_id(self, user_id: int) -> Dict[str, Any]:
        """根据ID获取用户。"""
        user = await self._dao.get_user_by_id(user_id)
        if user:
            return {"id": user.id, "name": user.name}
        return None

    async def update_user_name_by_id(self, user_id: int, new_name: str) -> Dict[str, Any]:
        """通过用户ID更新用户名。"""
        await self._dao.update_user_name(user_id, new_name)
        user = await self._dao.get_user_by_id(user_id)
        return {"id": user.id, "name": user.name}
