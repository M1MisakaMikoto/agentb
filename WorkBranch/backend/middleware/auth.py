from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from singleton import get_user_service
from controller.VO.result import Result


PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        
        if request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
            return await call_next(request)
        
        user_id_header = request.headers.get("X-User-ID")
        if not user_id_header:
            return JSONResponse(
                status_code=401,
                content=Result.error(message="未提供用户标识 (X-User-ID)", code=401).model_dump(),
            )
        
        try:
            user_id = int(user_id_header)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content=Result.error(message="用户标识格式错误，必须为整数", code=400).model_dump(),
            )
        
        user_service = get_user_service()
        user = await user_service.get_or_create_user(user_id)
        
        request.state.user = user
        return await call_next(request)
