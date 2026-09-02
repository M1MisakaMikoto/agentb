from contextlib import asynccontextmanager
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

# 将 WorkBranch/ 加入 sys.path，使 rag 包可被导入
_WORKBRANCH_ROOT = Path(__file__).resolve().parent.parent
if str(_WORKBRANCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKBRANCH_ROOT))

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from controller.VO.result import Result
from controller.user_api import router as user_router
from controller.session_api import router as session_router
from controller.conversation_api import router as conversation_router
from controller.workspace_api import router as workspace_router
from controller.plan_api import router as plan_router
from controller.settings_api import router as settings_router
from core.logging import bind_ctx, get_ctx, initialize_trace_writer
from singleton import clear_all_singletons_async, get_logging_runtime, get_settings_service, get_user_service
from middleware.auth import AuthMiddleware
from middleware.affinity import AffinityMiddleware
from service.runtime import OwnerConflictError, get_runtime_state
from rag.controller.file_controller import router as rag_router, on_rag_shutdown, on_rag_startup


for stream_name in ('stdout', 'stderr'):
    stream = getattr(sys, stream_name, None)
    reconfigure = getattr(stream, 'reconfigure', None)
    if callable(reconfigure):
        reconfigure(encoding='utf-8', errors='replace')


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_trace_writer()
    on_rag_startup()

    from singleton import get_mysql_database
    db = await get_mysql_database()
    await db.init_tables()
    await get_runtime_state().start()

    # 启动时自动注册已存在的workspace目录
    try:
        from singleton import get_workspace_service
        ws_service = get_workspace_service()
        import os, glob as _glob
        base = ws_service.base_dir
        if os.path.isdir(base):
            for session_dir in os.listdir(base):
                session_path = os.path.join(base, session_dir)
                if os.path.isdir(session_path):
                    for ws_dir in os.listdir(session_path):
                        ws_path = os.path.join(session_path, ws_dir)
                        if os.path.isdir(ws_path) and ws_dir not in ws_service._workspaces:
                            ws_service._workspaces[ws_dir] = {
                                "id": ws_dir,
                                "session_id": session_dir,
                                "status": "active",
                                "created_at": None,
                            }
                            print(f"[Workspace] auto-registered: {ws_dir} (session={session_dir})")
    except Exception as e:
        print(f"[Workspace] auto-registration failed: {e}")

    runtime = get_logging_runtime()
    app_logger = runtime.get_logger("app")
    runtime.start()
    app_logger.info(
        event="app.started",
        msg="logging runtime started",
        extra={
            "run_id": runtime.run_id,
            "log_dir": str(runtime.log_dir) if runtime.log_dir else None,
        },
    )
    try:
        yield
    finally:
        await get_runtime_state().stop()
        on_rag_shutdown()
        app_logger.info(
            event="app.stopping",
            msg="application stopping",
            extra={"run_id": runtime.run_id},
        )
        flushed = runtime.shutdown()
        if not flushed:
            app_logger.warning(
                event="app.flush_timeout",
                msg="logging runtime flush timed out",
                extra={"timeout_seconds": 3.0},
            )
        await clear_all_singletons_async()


app = FastAPI(
    lifespan=lifespan,
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "frontend", "description": "前端相关接口"},
        {"name": "health", "description": "健康检查"},
        {"name": "user", "description": "用户相关接口"},
        {"name": "session", "description": "会话相关接口"},
        {"name": "conversation", "description": "会话相关接口"},
        {"name": "workspace", "description": "工作区相关接口"},
        {"name": "rag", "description": "RAG相关接口"},
    ],
    openapi_prefix="",
)

# 重写openapi方法以添加安全方案
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    # 获取默认的OpenAPI schema
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title="API Documentation",
        version="1.0.0",
        description="API接口文档",
        routes=app.routes,
    )
    # 添加安全方案
    openapi_schema["components"] = {
        "securitySchemes": {
            "Bearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            },
            "X-User-ID": {
                "type": "apiKey",
                "name": "X-User-ID",
                "in": "header",
                "description": "用户ID"
            },
        },
    }
    # 为所有路由添加安全要求
    openapi_schema["security"] = [
        {
            "X-User-ID": [],
        },
    ]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

app.add_middleware(AuthMiddleware)
app.add_middleware(AffinityMiddleware)

# 静态托管 API 演示前端（纯静态，无构建）
_FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/frontend", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")

FRONTEND_LOG_ALLOWED_EVENTS = {
    "create_conversation",
    "delete_conversation",
    "switch_conversation",
    "send_message",
    "stream_completed",
    "stream_failed",
    "client.restored",
    "workspace.loaded",
    "auto_arrange_conversations",
    "move_conversation_node",
}
FRONTEND_LOG_ALLOWED_LEVELS = {"INFO", "WARNING", "ERROR"}
FRONTEND_LOG_MAX_PAYLOAD_BYTES = 8 * 1024
FRONTEND_LOG_MAX_MSG_LENGTH = 512
FRONTEND_LOG_MAX_EXTRA_BYTES = 4 * 1024
FRONTEND_LOG_RATE_LIMIT = 60
FRONTEND_LOG_RATE_WINDOW_SECONDS = 60.0
_frontend_log_requests: dict[str, list[float]] = {}

# 配置Bearer认证方案
security = HTTPBearer(auto_error=False)

# 用于FastAPI文档的依赖项
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    # 对于文档页面的访问，允许无token
    return None


class FrontendLogBody(BaseModel):
    level: str
    event: str
    msg: str | None = None
    extra: dict[str, Any] | None = None
    client_ts: str | None = None


async def _extract_request_business_ids(request: Request) -> tuple[str | None, str | None]:
    conversation_id = request.path_params.get("conversation_id")
    workspace_id = request.path_params.get("workspace_id") or request.query_params.get("workspace_id")

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return conversation_id, workspace_id

    try:
        body_bytes = await request.body()
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return conversation_id, workspace_id

    if isinstance(payload, dict):
        if conversation_id is None:
            raw_conversation_id = payload.get("conversation_id")
            if raw_conversation_id is not None:
                conversation_id = str(raw_conversation_id)
        if workspace_id is None:
            raw_workspace_id = payload.get("workspace_id")
            if raw_workspace_id is not None:
                workspace_id = str(raw_workspace_id)

    return conversation_id, workspace_id



def _is_same_origin_request(request: Request) -> bool:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    expected = urlparse(str(request.base_url))
    expected_netloc = expected.netloc
    expected_host = (expected.hostname or "").lower()
    local_hosts = {"127.0.0.1", "localhost"}

    def _matches(url: str) -> bool:
        parsed = urlparse(url)
        if not (parsed.scheme and parsed.netloc):
            return False
        if parsed.netloc == expected_netloc:
            return True

        parsed_host = (parsed.hostname or "").lower()
        return expected_host in local_hosts and parsed_host in local_hosts

    if origin:
        return _matches(origin)
    if referer:
        return _matches(referer)
    return True



def _allow_frontend_log_request(rate_key: str) -> bool:
    now = time.monotonic()
    window_start = now - FRONTEND_LOG_RATE_WINDOW_SECONDS
    bucket = [ts for ts in _frontend_log_requests.get(rate_key, []) if ts >= window_start]
    if len(bucket) >= FRONTEND_LOG_RATE_LIMIT:
        _frontend_log_requests[rate_key] = bucket
        return False
    bucket.append(now)
    _frontend_log_requests[rate_key] = bucket
    return True



def _build_frontend_log_extra(body: FrontendLogBody) -> dict[str, Any] | None:
    extra = dict(body.extra or {})
    if body.client_ts:
        extra["client_ts"] = body.client_ts
    return extra or None



def _get_logging_flags() -> dict[str, bool]:
    settings = get_settings_service()
    settings.reload()
    return {
        "logging_enabled": bool(settings.get("logging:enabled")),
        "frontend_enabled": bool(settings.get("logging:frontend:enabled")),
        "api_log_enabled": bool(settings.get("logging:api_log_enabled")),
    }


@app.post("/api/logs", tags=["frontend"])
@app.post("/logs", tags=["frontend"])
async def ingest_frontend_log(request: Request, body: FrontendLogBody) -> Result:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        raise HTTPException(status_code=415, detail="Unsupported Media Type")

    raw_body = await request.body()
    if len(raw_body) > FRONTEND_LOG_MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Payload Too Large")

    if not _is_same_origin_request(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    flags = _get_logging_flags()
    if not flags["logging_enabled"] or not flags["frontend_enabled"]:
        return Result.success()

    if body.level not in FRONTEND_LOG_ALLOWED_LEVELS:
        raise HTTPException(status_code=400, detail="Invalid log level")
    if body.event not in FRONTEND_LOG_ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="Invalid log event")
    if body.msg is not None and len(body.msg) > FRONTEND_LOG_MAX_MSG_LENGTH:
        raise HTTPException(status_code=400, detail="Log message too long")

    extra = _build_frontend_log_extra(body)
    if extra is not None:
        extra_bytes = len(json.dumps(extra, ensure_ascii=False).encode("utf-8"))
        if extra_bytes > FRONTEND_LOG_MAX_EXTRA_BYTES:
            raise HTTPException(status_code=400, detail="Log extra too large")

    client_id = getattr(request.state, "client_id", None)
    remote_addr = request.client.host if request.client else "unknown"
    rate_key = client_id or remote_addr
    if not _allow_frontend_log_request(rate_key):
        raise HTTPException(status_code=429, detail="Too Many Requests")

    logger = get_logging_runtime().get_logger("frontend")
    log_method = {
        "INFO": logger.info,
        "WARNING": logger.warning,
        "ERROR": logger.error,
    }[body.level]
    log_method(event=body.event, msg=body.msg, extra=extra)
    return Result.success()


# 临时注释以测试流式响应 - logging_middleware (BaseHTTPMiddleware) 会缓冲
# @app.middleware("http")
async def logging_middleware(request: Request, call_next):
    runtime = get_logging_runtime()
    logger = runtime.get_logger("api")
    request_id = str(uuid4())
    client_id = request.headers.get("X-Client-Id") or None
    conversation_id, workspace_id = await _extract_request_business_ids(request)
    request.state.request_id = request_id
    request.state.client_id = client_id
    request.state.conversation_id = conversation_id
    request.state.workspace_id = workspace_id

    user_id = None
    try:
        user = get_user_service().get_current_user()
        if user is not None:
            user_id = str(user.id)
    except Exception:
        user_id = None
    request.state.user_id = user_id

    start_time = time.perf_counter()
    remote_addr = request.client.host if request.client else None
    query_keys = sorted(request.query_params.keys())
    flags = _get_logging_flags()
    api_log_enabled = flags["logging_enabled"] and flags["api_log_enabled"]

    with bind_ctx(
        client_id=client_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=request_id,
    ):
        if api_log_enabled:
            logger.info(
                event="request.started",
                msg="request started",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "query_keys": query_keys,
                    "remote_addr": remote_addr,
                },
            )
        try:
            response = await call_next(request)
        except Exception:
            if api_log_enabled:
                logger.info(
                    event="request.completed",
                    msg="request completed",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": 500,
                        "latency_ms": round((time.perf_counter() - start_time) * 1000),
                    },
                )
            raise

        response.headers["X-Request-Id"] = request_id
        if api_log_enabled:
            logger.info(
                event="request.completed",
                msg="request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "latency_ms": round((time.perf_counter() - start_time) * 1000),
                },
            )
        return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    print(f"[ERROR] Unhandled exception: {type(exc).__name__}: {exc}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    
    try:
        with open('error_details.log', 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Request: {request.method} {request.url.path}\n")
            f.write(f"Exception: {type(exc).__name__}: {exc}\n")
            f.write(f"Traceback:\n{traceback.format_exc()}\n")
    except Exception as log_error:
        print(f"[ERROR] Failed to write error log: {log_error}", file=sys.stderr)
    
    try:
        runtime = get_logging_runtime()
        request_ctx = {
            "client_id": getattr(request.state, "client_id", None),
            "conversation_id": getattr(request.state, "conversation_id", None),
            "workspace_id": getattr(request.state, "workspace_id", None),
            "user_id": getattr(request.state, "user_id", None),
            "request_id": getattr(request.state, "request_id", None),
        }
        with bind_ctx(**request_ctx):
            runtime.get_logger("app").error(
                event="error.unhandled_exception",
                msg="unhandled exception in api request",
                extra={
                    "scope": "api",
                    "method": request.method,
                    "path": request.url.path,
                },
                exception="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
    except Exception:
        pass
    response = JSONResponse(
        status_code=500,
        content={"code": 500, "message": "Internal Server Error", "data": None},
    )
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(OwnerConflictError)
async def owner_conflict_handler(request: Request, exc: OwnerConflictError):
    return JSONResponse(
        status_code=409,
        content={
            "code": 409,
            "message": str(exc),
            "data": {"session_id": exc.session_id, "owner_instance_id": exc.owner_instance_id},
        },
        headers={"X-AgentB-Owner-ID": exc.owner_instance_id},
    )


@app.get("/health", tags=["health"])
def health_check():
    """增强的健康检查端点 - 返回资源使用情况"""
    try:
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        
        runtime_state = get_runtime_state()
        return {
            "status": "ok",
            "instance_id": runtime_state.instance_id,
            "draining": runtime_state.draining,
            "active_tasks": runtime_state.active_task_count,
            "active_sessions": runtime_state.active_session_count,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "resources": {
                "memory_mb": round(memory_info.rss / 1024 / 1024, 2),
                "cpu_percent": process.cpu_percent(),
                "threads": process.num_threads(),
                "open_files": len(process.open_files()) if hasattr(process, 'open_files') else 0,
            },
            "uptime_seconds": int(time.time() - _start_time) if '_start_time' in dir() else None,
        }
    except ImportError:
        runtime_state = get_runtime_state()
        return {"status": "ok", "instance_id": runtime_state.instance_id, "draining": runtime_state.draining, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        return {"status": "warning", "message": str(e), "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/health/ready", tags=["health"])
def readiness_check():
    runtime_state = get_runtime_state()
    if runtime_state.draining:
        return JSONResponse(
            status_code=503,
            content={
                "status": "draining",
                "instance_id": runtime_state.instance_id,
                "active_tasks": runtime_state.active_task_count,
            },
        )
    return {
        "status": "ready",
        "instance_id": runtime_state.instance_id,
        "active_tasks": runtime_state.active_task_count,
    }


@app.post("/admin/drain", tags=["health"])
async def begin_drain():
    runtime_state = get_runtime_state()
    await runtime_state.begin_drain()
    return {
        "status": "draining",
        "instance_id": runtime_state.instance_id,
        "active_tasks": runtime_state.active_task_count,
    }


app.include_router(user_router)
app.include_router(session_router)
app.include_router(conversation_router)
app.include_router(workspace_router)
app.include_router(plan_router)
app.include_router(settings_router)
app.include_router(rag_router)
