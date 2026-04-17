import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

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


class AuthMiddleware(BaseHTTPMiddleware):
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

        return await call_next(request)
