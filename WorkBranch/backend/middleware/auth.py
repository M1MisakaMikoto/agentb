from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from controller.VO.result import Result


PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
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