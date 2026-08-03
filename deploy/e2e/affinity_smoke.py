"""Black-box affinity smoke test for a running AgentB Compose deployment.

Run after Compose is healthy:
    python deploy/e2e/affinity_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("AGENTB_E2E_BASE_URL", "http://127.0.0.1:8152").rstrip("/")
USER_ID = os.getenv("AGENTB_E2E_USER_ID", "910001")
SESSION_COUNT = int(os.getenv("AGENTB_E2E_SESSION_COUNT", "12"))


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: Any


def request(
    method: str,
    path: str,
    *,
    affinity: str | int | None = None,
    body: dict | None = None,
) -> Response:
    headers = {"X-User-ID": USER_ID, "Accept": "application/json"}
    if affinity is not None:
        headers["X-AgentB-Affinity-Key"] = str(affinity)
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(f"{BASE_URL}{path}", data=payload, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=30)
    except HTTPError as exc:
        response = exc
    raw = response.read()
    parsed = json.loads(raw.decode("utf-8")) if raw else None
    return Response(
        response.status,
        {key.lower(): value for key, value in response.headers.items()},
        parsed,
    )


def expect_status(response: Response, expected: int, label: str) -> None:
    if response.status != expected:
        raise AssertionError(
            f"{label}: expected HTTP {expected}, got {response.status}: {response.body}"
        )


def data(response: Response) -> Any:
    if not isinstance(response.body, dict):
        raise AssertionError(f"Expected JSON envelope, got {response.body!r}")
    return response.body.get("data")


def main() -> int:
    created_sessions: list[int] = []
    routed_instances: set[str] = set()
    try:
        health = request("GET", "/router-health")
        expect_status(health, 200, "router health")

        for index in range(SESSION_COUNT):
            provisional_key = str(uuid.uuid4())
            created = request(
                "POST",
                f"/api/session/sessions?title=e2e-{index}",
                affinity=provisional_key,
            )
            expect_status(created, 200, "create session")
            session_id = int(data(created)["id"])
            created_sessions.append(session_id)

            idempotency_key = str(uuid.uuid4())
            conversation = request(
                "POST",
                f"/api/session/sessions/{session_id}/conversations",
                affinity=session_id,
                body={"user_content": "affinity smoke", "idempotency_key": idempotency_key},
            )
            expect_status(conversation, 200, "create conversation")
            instance_id = conversation.headers.get("x-agentb-instance-id")
            if not instance_id:
                raise AssertionError("create conversation response has no instance diagnostic header")
            routed_instances.add(instance_id)
            conversation_id = str(data(conversation)["conversation_id"])

            duplicate = request(
                "POST",
                f"/api/session/sessions/{session_id}/conversations",
                affinity=session_id,
                body={"user_content": "affinity smoke", "idempotency_key": idempotency_key},
            )
            expect_status(duplicate, 200, "idempotent create conversation")
            if data(duplicate)["conversation_id"] != conversation_id:
                raise AssertionError("idempotency key produced a second conversation")
            if duplicate.headers.get("x-agentb-instance-id") != instance_id:
                raise AssertionError("same session was routed to a different instance")

            missing = request(
                "POST",
                f"/api/session/conversations/{conversation_id}/cancel",
            )
            expect_status(missing, 400, "missing affinity key")

            mismatched = request(
                "POST",
                f"/api/session/conversations/{conversation_id}/cancel",
                affinity=f"wrong-{session_id}",
            )
            expect_status(mismatched, 409, "mismatched affinity key")

            cancelled = request(
                "POST",
                f"/api/session/conversations/{conversation_id}/cancel",
                affinity=session_id,
            )
            expect_status(cancelled, 200, "cancel conversation")
            if cancelled.headers.get("x-agentb-instance-id") != instance_id:
                raise AssertionError("cancel request did not reach the session owner")

        if SESSION_COUNT >= 6 and len(routed_instances) < 2:
            raise AssertionError(
                f"{SESSION_COUNT} session keys only reached {sorted(routed_instances)}"
            )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "base_url": BASE_URL,
                    "sessions": len(created_sessions),
                    "instances": sorted(routed_instances),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        for session_id in created_sessions:
            response = request(
                "DELETE",
                f"/api/session/sessions/{session_id}",
                affinity=session_id,
            )
            if response.status not in {200, 404}:
                print(
                    f"cleanup failed for session {session_id}: {response.status} {response.body}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
