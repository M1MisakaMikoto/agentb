"""AgentB 对外接入示例客户端（生产口径）。

覆盖链路：建会话 → 建对话（幂等键）→ SSE 接流（忽略心跳、按 seq 断点续传、
识别终态）→ agent 反问时回填 resume → 取最终结果。

依赖：httpx、httpx-sse（均在 AgentB 的 requirements.txt 中）。

用法：
    set AGENTB_BASE_URL=http://127.0.0.1:8000    # 经 Nginx 路由时形如 http://host:8152/api
    set AGENTB_USER_ID=1
    python agentb_client.py "用一句话说明你能做什么"
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Callable, Optional

import httpx
from httpx_sse import connect_sse

TERMINAL_TYPES = {"done", "error", "cancelled"}
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=60.0)


def headers(user_id: str, affinity: Optional[str] = None) -> dict:
    """鉴权头 + 实例亲和键（写操作与 /stream 必须带，键值=session_id）。"""
    result = {"X-User-ID": str(user_id)}
    if affinity is not None:
        result["X-AgentB-Affinity-Key"] = str(affinity)
    return result


def raise_for_error(response: httpx.Response) -> dict:
    """统一处理两种错误结构：{code,message,data} 与 {"detail": ...}。"""
    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text}
        message = payload.get("message") or payload.get("detail") or response.text
        raise RuntimeError(f"HTTP {response.status_code}: {message}")
    return response.json()


def create_session(client: httpx.Client, user_id: str, title: str) -> dict:
    response = client.post(
        "/session/sessions",
        headers=headers(user_id, affinity=1),  # 创建会话时亲和键先占位为 1，返回后用真实 session_id
        json={"title": title},
    )
    return raise_for_error(response)["data"]


def create_conversation(
    client: httpx.Client,
    user_id: str,
    session_id: str,
    user_content: str,
    idempotency_key: Optional[str] = None,
) -> str:
    body = {"user_content": user_content}
    if idempotency_key:
        body["idempotency_key"] = idempotency_key
    response = client.post(
        f"/session/sessions/{session_id}/conversations",
        headers=headers(user_id, affinity=session_id),
        json=body,
    )
    return raise_for_error(response)["data"]["conversation_id"]


def upload_file(client: httpx.Client, user_id: str, workspace_id: str, file_path: str) -> dict:
    path = os.path.abspath(file_path)
    with open(path, "rb") as handle:
        response = client.post(
            f"/workspaces/{workspace_id}/files",
            headers=headers(user_id),
            files={"files": (os.path.basename(path), handle, "application/octet-stream")},
        )
    return raise_for_error(response)["data"]


def resume(client: httpx.Client, user_id: str, session_id: str, conversation_id: str, answer: str, call_seq: Optional[int]) -> dict:
    body = {"answer": answer}
    if call_seq is not None:
        body["call_seq"] = call_seq
    response = client.post(
        f"/session/conversations/{conversation_id}/resume",
        headers=headers(user_id, affinity=session_id),
        json=body,
    )
    return raise_for_error(response)["data"]


def get_conversation(client: httpx.Client, user_id: str, conversation_id: str) -> dict:
    response = client.get(
        f"/session/conversations/{conversation_id}",
        headers=headers(user_id),
    )
    return raise_for_error(response)["data"]


def stream_conversation(
    client: httpx.Client,
    user_id: str,
    session_id: str,
    conversation_id: str,
    answer_provider: Callable[[dict], str],
    mode: str = "interactive",
    max_reconnect: int = 3,
) -> str:
    """接流直到终态；断线按已收到的最大 seq 续传；agent 反问时自动回填。

    返回本次会话产出的最终文本（正文 text_delta 与会话式 chat_delta 都计入）。
    """
    url = f"/session/conversations/{conversation_id}/stream"
    last_seq = 0
    text_parts: list[str] = []
    attempt = 0

    while attempt <= max_reconnect:
        attempt += 1
        terminal = False
        try:
            with connect_sse(
                client,
                "GET",
                url,
                params={"last_seq": last_seq, "mode": mode},
                headers=headers(user_id, affinity=session_id),
                timeout=TIMEOUT,
            ) as event_source:
                for event in event_source.iter_sse():
                    if not event.data:  # 心跳（": heartbeat"）等注释帧
                        continue
                    payload = json.loads(event.data)
                    event_type = payload.get("type")
                    seq = payload.get("seq")
                    if isinstance(seq, int):
                        last_seq = max(last_seq, seq)

                    if event_type in {"text_delta", "chat_delta"}:
                        text_parts.append(payload.get("content") or "")
                    elif event_type == "system_alert":
                        print(f"[alert] {payload.get('content')}", file=sys.stderr)
                    elif event_type == "user_input_request":
                        request_payload = json.loads(payload.get("content") or "{}")
                        answer = answer_provider(request_payload)
                        resume(
                            client,
                            user_id,
                            session_id,
                            conversation_id,
                            answer,
                            request_payload.get("call_seq"),
                        )
                    elif event_type in TERMINAL_TYPES:
                        terminal = True
                        if event_type != "done":
                            print(f"[terminal:{event_type}] {payload.get('content')}", file=sys.stderr)
                        break
        except (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.TransportError) as exc:
            print(f"[reconnect] {type(exc).__name__}: {exc} (last_seq={last_seq})", file=sys.stderr)
            continue

        if terminal:
            break

    return "".join(text_parts)


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "用一句话说明你能做什么"
    base_url = os.environ.get("AGENTB_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    user_id = os.environ.get("AGENTB_USER_ID", "1")

    with httpx.Client(base_url=base_url, timeout=TIMEOUT) as client:
        session = create_session(client, user_id, f"对接验证 {time.strftime('%H:%M:%S')}")
        session_id, workspace_id = str(session["id"]), session["workspace_id"]
        print(f"session_id={session_id} workspace_id={workspace_id}")

        conversation_id = create_conversation(
            client,
            user_id,
            session_id,
            prompt,
            idempotency_key=f"cli-{int(time.time() * 1000)}",
        )
        print(f"conversation_id={conversation_id}")

        answer = stream_conversation(
            client,
            user_id,
            session_id,
            conversation_id,
            answer_provider=lambda payload: input(f"[agent 提问] {payload.get('question')}\n> "),
        )
        print("\n--- 回答 ---")
        print(answer or "(空)")

        final = get_conversation(client, user_id, conversation_id)
        print(f"\n--- 终态 --- state={final.get('state')} error={final.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
