from __future__ import annotations

from urllib.parse import parse_qs

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from controller.VO.result import Result
from service.runtime.affinity import AffinityError, get_runtime_state, validate_affinity_key


AFFINITY_HEADER = b"x-agentb-affinity-key"


def _normalized_path(path: str) -> str:
    return path[4:] if path.startswith("/api/") else path


def _requires_affinity(scope: Scope) -> bool:
    path = _normalized_path(scope.get("path", ""))
    method = scope.get("method", "GET").upper()
    if path.startswith("/session/conversations/"):
        return method in {"POST", "PUT", "PATCH", "DELETE"} or path.endswith("/stream")
    if path.startswith("/session/sessions/"):
        return method in {"POST", "PUT", "PATCH", "DELETE"}
    return path == "/session/sessions" and method == "POST"


def _is_new_session_request(scope: Scope) -> bool:
    return (
        _normalized_path(scope.get("path", "")) == "/session/sessions"
        and scope.get("method", "GET").upper() == "POST"
    )


class AffinityMiddleware:
    """Validate the routing contract and add per-instance diagnostics."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        runtime = get_runtime_state()
        headers = dict(scope.get("headers", []))
        query = parse_qs(scope.get("query_string", b"").decode("utf-8", "replace"))
        raw_header = headers.get(AFFINITY_HEADER)
        raw_key = raw_header.decode("utf-8", "replace") if raw_header else None
        raw_key = raw_key or (query.get("affinity_key") or [None])[0]

        if _requires_affinity(scope):
            try:
                affinity_key = validate_affinity_key(raw_key)
            except AffinityError as exc:
                response = JSONResponse(
                    status_code=400,
                    content=Result.error(message=str(exc), code=400).model_dump(),
                    headers={"X-AgentB-Instance-ID": runtime.instance_id},
                )
                await response(scope, receive, send)
                return
            scope.setdefault("state", {})["affinity_key"] = affinity_key

        if runtime.draining and _is_new_session_request(scope):
            response = JSONResponse(
                status_code=503,
                content=Result.error(
                    message="Instance is draining and cannot accept new sessions", code=503
                ).model_dump(),
                headers={
                    "Retry-After": "5",
                    "X-AgentB-Instance-ID": runtime.instance_id,
                },
            )
            await response(scope, receive, send)
            return

        async def send_with_instance_id(message):
            if message.get("type") == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (b"x-agentb-instance-id", runtime.instance_id.encode("ascii", "replace"))
                )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_with_instance_id)
