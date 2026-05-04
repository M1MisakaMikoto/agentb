import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send, Message
from fastapi import Request
from fastapi.responses import JSONResponse

from controller.VO.result import Result


PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
}
RAG_UI_PUBLIC_PATHS = {
    "/rag",
    "/rag/",
}

AUTH_DISABLED_ENV = "AUTH_DISABLED"


def _is_auth_disabled() -> bool:
    value = os.getenv(AUTH_DISABLED_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


class AuthMiddleware:
    """纯 ASGI 中间件 - 不会缓冲 StreamingResponse"""
    
    def __init__(self, app: ASGIApp):
        self.app = app
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        if _is_auth_disabled():
            # 即使禁用认证，也要设置默认用户信息
            scope["state"] = getattr(scope, "state", {})
            scope["state"]["user"] = {
                "id": 1,
                "name": "default_user"
            }
            await self.app(scope, receive, send)
            return
        
        path = scope.get("path", "")
        if (
            path in PUBLIC_PATHS
            or path in RAG_UI_PUBLIC_PATHS
            or path.startswith("/docs")
            or path.startswith("/openapi")
        ):
            await self.app(scope, receive, send)
            return
        
        # 从 ASGI scope 中提取 headers
        headers = dict(scope.get("headers", []))
        user_id_header = headers.get(b"x-user-id")
        
        if not user_id_header:
            response = JSONResponse(
                status_code=401,
                content=Result.error(message="未提供 X-User-ID", code=401).model_dump(),
            )
            await response(scope, receive, send)
            return
        
        try:
            user_id = int(user_id_header.decode())
        except (ValueError, AttributeError):
            response = JSONResponse(
                status_code=401,
                content=Result.error(message="X-User-ID 必须是整数", code=401).model_dump(),
            )
            await response(scope, receive, send)
            return
        
        # 将用户信息存储在 scope 的 state 中
        scope["state"] = getattr(scope, "state", {})
        scope["state"]["user"] = {
            "id": user_id,
            "name": f"user_{user_id}"
        }
        
        await self.app(scope, receive, send)


# 保留 BaseHTTPMiddleware 版本以供兼容（已弃用）
class AuthMiddlewareDeprecated(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_auth_disabled():
            return await call_next(request)

        path = request.url.path
        if (
            path in PUBLIC_PATHS
            or path in RAG_UI_PUBLIC_PATHS
            or path.startswith("/docs")
            or path.startswith("/openapi")
        ):
            return await call_next(request)

        user_id = request.headers.get("X-User-ID")
        if not user_id:
            return JSONResponse(
                status_code=401,
                content=Result.error(message="未提供 X-User-ID", code=401).model_dump(),
            )

        try:
            user_id = int(user_id)
        except ValueError:
            return JSONResponse(
                status_code=401,
                content=Result.error(message="X-User-ID 必须是整数", code=401).model_dump(),
            )

        request.state.user = {
            "id": user_id,
            "name": f"user_{user_id}"
        }

        response = await call_next(request)
        
        # 对于流式响应，确保不缓冲
        if hasattr(response, 'body_iterator'):
            response.headers.setdefault("Cache-Control", "no-cache")
            response.headers.setdefault("X-Accel-Buffering", "no")
        
        return response
