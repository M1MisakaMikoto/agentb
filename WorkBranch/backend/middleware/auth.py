from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from jose import JWTError, jwt
from datetime import datetime

from singleton import get_user_service
from controller.VO.result import Result


PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
}

# 随便填，根本用不到
SECRET_KEY = "none"
ALGORITHM = "HS256"


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 放行公开路径
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
            return await call_next(request)

        # ==========================
        # 直接获取 token（无 Bearer）
        # ==========================
        token = request.headers.get("Authorization")
        if not token:
            return JSONResponse(
                status_code=401,
                content=Result.error(message="未提供Token", code=401).model_dump(),
            )

        # ==========================
        # 直接解析内容，不验证
        # ==========================
        try:
            payload = jwt.get_unverified_claims(token)

            # 解析出的信息
            user_id = payload.get("id")
            user_name = payload.get("name")

            if not user_id:
                raise JWTError("token中无用户ID")

            # 存入请求上下文
            request.state.user = {
                "id": user_id,
                "name": user_name
            }

        except Exception as e:
            return JSONResponse(
                status_code=401,
                content=Result.error(message=f"Token解析失败: {str(e)}", code=401).model_dump(),
            )

        return await call_next(request)