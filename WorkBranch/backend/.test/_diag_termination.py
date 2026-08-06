"""
回归辅助：验证对话能否正常结束（正常路径 / 错误路径）。

用法（backend 目录）：
    PYTHONIOENCODING=utf-8 python .test/_diag_termination.py
"""

import asyncio
import json
import os
import sys
import time


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)

from test_cases.base import APIClient, extract_response_text, load_config, wait_for_conversation_state


async def run_case(api: APIClient, title: str, message: str, timeout: float = 180.0):
    print(f"\n=== CASE: {title} ===")
    print(f"message: {message}")

    session_resp = await api.create_session(title=title)
    if not session_resp.get("success"):
        print(f"FAIL: create_session: {session_resp}")
        return False
    session_id = (session_resp.get("data") or {}).get("id")
    workspace_id = (session_resp.get("data") or {}).get("workspace_id")
    print(f"session={session_id} workspace={workspace_id}")

    conv_resp = await api.create_conversation(session_id, message)
    if not conv_resp.get("success"):
        print(f"FAIL: create_conversation: {conv_resp}")
        return False
    conv_id = (conv_resp.get("data") or {}).get("conversation_id") or (conv_resp.get("data") or {}).get("id")
    print(f"conversation={conv_id}")

    # 打开 stream 触发处理并消费事件（与 E2E runner 行为一致）
    stream_types: list[str] = []
    stream_text = ""
    deadline = time.time() + timeout
    try:
        async for line in api.stream_message(conv_id, last_seq=0):
            if time.time() > deadline:
                print("stream timeout")
                break
            line = (line or {}).get("raw_line", "")
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload:
                continue
            try:
                event = json.loads(payload)
            except Exception:
                continue
            etype = event.get("type", "")
            stream_types.append(etype)
            if etype == "chat_delta":
                stream_text += str(event.get("content") or "")
            if etype in ("done", "error"):
                break
    except Exception as e:
        print(f"stream exception: {type(e).__name__}: {e}")

    print(f"stream types: {stream_types[:12]}{'...' if len(stream_types) > 12 else ''}")
    print(f"stream text preview: {stream_text[:150]!r}")

    final = await wait_for_conversation_state(api, conv_id, "completed", timeout=60.0)
    state = (final.get("data") or {}).get("state")
    print(f"terminal state: {state}")

    conv = await api.get_conversation(conv_id)
    data = conv.get("data") or {}
    text = extract_response_text(conv)
    print(f"assistant_content_len={len(data.get('assistant_content') or '')} text_preview={text[:200]!r}")
    print(f"error={data.get('error')}")

    ok = state == "completed" and bool(text.strip()) and "done" in stream_types
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


async def main():
    config = load_config()
    api = APIClient(config)

    cases = [
        (
            "normal_chat_ends_completed",
            "你好，请简单介绍一下你自己，一两句话即可。",
        ),
        (
            "error_path_blocked_ends_completed",
            "请读取工作区中不存在的文件 report_xyz_2026.txt 的内容并总结，如果文件不存在请明确告诉我阻塞原因。",
        ),
    ]

    results = []
    for title, message in cases:
        try:
            results.append(await run_case(api, title, message))
        except Exception as e:
            print(f"CASE {title} EXCEPTION: {type(e).__name__}: {e}")
            results.append(False)

    print("\n=== SUMMARY ===")
    for (title, _msg), ok in zip(cases, results):
        print(f"  {title}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
