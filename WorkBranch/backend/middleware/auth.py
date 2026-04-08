from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from singleton import get_user_service
from controller.VO.result import Result


PUBLIC_PATHS = {
    "/user/register",
    "/user/login",
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
        
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content=Result.error(message="未提供认证令牌", code=401).model_dump(),
            )
        
        token = auth_header[7:]
        user_service = get_user_service()
        user = await user_service.validate_token(token)
        
        if not user:
            return JSONResponse(
                status_code=401,
                content=Result.error(message="无效的认证令牌", code=401).model_dump(),
            )
        
        request.state.user = user
        return await call_next(request)
