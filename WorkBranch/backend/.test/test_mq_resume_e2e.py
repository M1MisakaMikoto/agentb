#!/usr/bin/env python3
"""
Message Queue E2E Test - 断点续传功能测试

测试场景:
1. 正常流式传输 - 验证消息按序到达
2. 断点续传 - 中途断开后重新连接，从断点继续
3. 对话完成后重连 - 验证 is_completed 状态
4. 多订阅者并发 - 验证消息广播正确性

Usage:
    python test_mq_resume_e2e.py [--no-server]
"""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import URLError
from urllib.request import urlopen

import httpx

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    MAGENTA = "\033[35m"


class StreamResult:
    def __init__(self):
        self.messages: List[Dict] = []
        self.sequences: List[int] = []
        self.errors: List[str] = []
        self.done = False
        self.last_seq = 0
        self.raw_lines: List[str] = []

    def to_dict(self) -> dict:
        return {
            "message_count": len(self.messages),
            "sequences": self.sequences,
            "errors": self.errors,
            "done": self.done,
            "last_seq": self.last_seq,
        }


class APIClient:
    def __init__(self, base_url: str, user_id: int):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-User-ID": str(self.user_id),
        }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.request(method, url, headers=self._headers(), **kwargs)
                try:
                    data = response.json()
                    if response.status_code >= 400:
                        return {
                            "code": response.status_code,
                            "message": data.get("detail", str(data)),
                            "data": None,
                        }
                    return data
                except Exception as parse_err:
                    return {"code": response.status_code, "message": f"Parse error: {parse_err}, text: {response.text[:200]}", "data": None}
            except Exception as e:
                return {"code": -1, "message": f"Request error: {e}", "data": None}

    async def create_session(self, title: str) -> dict:
        return await self._request("POST", "/session/sessions", json={"title": title})

    async def create_conversation(self, session_id: int, user_content: str) -> dict:
        return await self._request(
            "POST",
            f"/session/sessions/{session_id}/conversations",
            json={"user_content": user_content},
        )

    async def get_conversation(self, conversation_id: str) -> dict:
        return await self._request("GET", f"/session/conversations/{conversation_id}")

    async def stream_message(self, conversation_id: str, last_seq: int = 0):
        url = f"{self.base_url}/session/conversations/{conversation_id}/stream?last_seq={last_seq}"
        timeout = httpx.Timeout(connect=30.0, read=None, write=300.0, pool=300.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", url, headers=self._headers()) as response:
                    if response.status_code != 200:
                        try:
                            error = await response.aread()
                            yield {"type": "error", "raw": error.decode(), "status_code": response.status_code}
                        except Exception as e:
                            yield {"type": "error", "raw": str(e), "status_code": response.status_code}
                        return

                    async for line in response.aiter_lines():
                        yield {"raw_line": line}
        except Exception as e:
            yield {"type": "error", "raw": f"Connection error: {e}", "status_code": 0}


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sanitized = text.encode(encoding, errors="replace").decode(encoding)
        print(sanitized)


def wait_for_backend(host: str = "127.0.0.1", port: int = 8000, timeout: float = 30.0) -> bool:
    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout

    print(f"{Colors.CYAN}Waiting for backend...{Colors.ENDC}")

    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    print(f"{Colors.GREEN}Backend ready{Colors.ENDC}")
                    return True
        except URLError:
            pass
        time.sleep(0.5)

    print(f"{Colors.RED}Backend timeout{Colors.ENDC}")
    return False


def start_backend() -> Optional[subprocess.Popen]:
    backend_dir = Path(__file__).parent.parent
    python_executable = sys.executable

    command = [
        python_executable,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]

    print(f"{Colors.CYAN}Starting backend...{Colors.ENDC}")

    kwargs = {
        "cwd": str(backend_dir),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)

    def stream_output():
        assert process.stdout is not None
        stdout_encoding = sys.stdout.encoding or "utf-8"
        for line in process.stdout:
            safe_line = line.rstrip().encode(stdout_encoding, errors="replace").decode(stdout_encoding)
            print(f"{Colors.DIM}[backend] {safe_line}{Colors.ENDC}")

    thread = threading.Thread(target=stream_output, daemon=True)
    thread.start()

    return process


def stop_backend(process: subprocess.Popen):
    if process.poll() is not None:
        return

    print(f"{Colors.CYAN}Stopping backend...{Colors.ENDC}")

    try:
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                process.terminate()
        else:
            process.terminate()

        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(f"{Colors.YELLOW}Force killing backend{Colors.ENDC}")
        process.kill()
        process.wait(timeout=5)

    print(f"{Colors.GREEN}Backend stopped{Colors.ENDC}")


async def wait_for_conversation_state(
    api: APIClient,
    conversation_id: str,
    expected_state: str,
    timeout: float = 180.0,
    initial_delay: float = 2.0,
) -> dict:
    await asyncio.sleep(initial_delay)
    deadline = time.time() + timeout
    last_result = None
    check_count = 0
    while time.time() < deadline:
        check_count += 1
        conversation_result = await api.get_conversation(conversation_id)
        last_result = conversation_result
        data = conversation_result.get("data") or {}
        current_state = data.get("state", "unknown")
        if check_count % 10 == 0:
            print(f"{Colors.DIM}    [check {check_count}] state={current_state}, expected={expected_state}{Colors.ENDC}")
        if current_state == expected_state:
            return conversation_result
        await asyncio.sleep(0.5)
    print(f"{Colors.YELLOW}    Timeout after {check_count} checks, final state={last_result.get('data', {}).get('state', 'unknown')}{Colors.ENDC}")
    return last_result


async def collect_stream_with_seq(
    api: APIClient,
    conversation_id: str,
    last_seq: int = 0,
    max_messages: int = None,
    verbose: bool = True,
) -> StreamResult:
    result = StreamResult()
    message_count = 0

    async for item in api.stream_message(conversation_id, last_seq):
        if item.get("type") == "error":
            error_msg = item.get("raw", "Unknown error")
            result.errors.append(error_msg)
            if verbose:
                print(f"{Colors.RED}[error] {error_msg}{Colors.ENDC}")
            continue

        raw_line = item.get("raw_line", "")
        if not raw_line.strip():
            continue

        result.raw_lines.append(raw_line)

        if raw_line.startswith(": heartbeat"):
            if verbose:
                print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
            continue

        if raw_line.startswith("data: "):
            json_str = raw_line[6:]

            try:
                data = json.loads(json_str)
                event_type = data.get("type", "unknown")
                seq = data.get("seq", 0)

                if seq > 0:
                    result.sequences.append(seq)
                    result.last_seq = seq

                if event_type == "chat_delta":
                    content = data.get("content", "")
                    result.messages.append({"type": "chat", "seq": seq, "content": content})
                    message_count += 1
                    if verbose:
                        safe_print(f"{Colors.GREEN}[chat:{seq}] {content}{Colors.ENDC}")
                elif event_type == "text_delta":
                    content = data.get("content", "")
                    result.messages.append({"type": "text", "seq": seq, "content": content})
                    message_count += 1
                    if verbose:
                        safe_print(f"{Colors.CYAN}[text:{seq}] {content}{Colors.ENDC}")
                elif event_type == "thinking_delta":
                    content = data.get("content", "")
                    result.messages.append({"type": "thinking", "seq": seq, "content": content})
                    message_count += 1
                    if verbose:
                        safe_print(f"{Colors.MAGENTA}[think:{seq}] {content[:50]}...{Colors.ENDC}")
                elif event_type == "tool_call":
                    metadata = data.get("metadata", {})
                    tool_name = metadata.get("tool_name", "unknown")
                    result.messages.append({"type": "tool", "seq": seq, "tool": tool_name})
                    if verbose:
                        print(f"{Colors.YELLOW}[tool:{seq}] {tool_name}{Colors.ENDC}")
                elif event_type == "done":
                    result.done = True
                    if verbose:
                        print(f"{Colors.GREEN}[done] Stream completed at seq={seq}{Colors.ENDC}")
                elif event_type == "error":
                    error_content = data.get("content", "Unknown error")
                    result.errors.append(error_content)
                    if verbose:
                        print(f"{Colors.RED}[error] {error_content}{Colors.ENDC}")
                else:
                    if verbose:
                        print(f"{Colors.BLUE}[{event_type}:{seq}]{Colors.ENDC}")

                if max_messages and message_count >= max_messages:
                    break

            except json.JSONDecodeError as e:
                parse_error = f"JSON parse error: {e}"
                result.errors.append(parse_error)
                if verbose:
                    print(f"{Colors.RED}{parse_error}{Colors.ENDC}")

    return result


async def test_normal_stream(api: APIClient, output_file: str) -> Dict:
    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test 1: Normal Stream{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    errors: List[str] = []
    output_lines: List[str] = []

    output_lines.append("# Test 1: Normal Stream")
    output_lines.append(f"- timestamp: {get_timestamp()}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("MQ Normal Stream Test")
    if session_result.get("code") != 200:
        error_msg = f"Session creation failed: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    session_id = session_result["data"]["id"]
    workspace_id = session_result["data"]["workspace_id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    output_lines.append(f"- session_id: {session_id}")
    output_lines.append(f"- workspace_id: {workspace_id}")

    print(f"{Colors.CYAN}[Step 2] Creating conversation...{Colors.ENDC}")
    conv_result = await api.create_conversation(
        session_id,
        "请用至少100字介绍一下Python的异步编程。"
    )
    if conv_result.get("code") != 200:
        error_msg = f"Conversation creation failed: {conv_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    conversation_id = conv_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")
    output_lines.append(f"- conversation_id: {conversation_id}")

    print(f"{Colors.CYAN}[Step 3] Receiving stream...{Colors.ENDC}\n")
    stream_result = await collect_stream_with_seq(api, conversation_id, verbose=True)

    output_lines.append("## Stream Result")
    output_lines.append(json.dumps(stream_result.to_dict(), ensure_ascii=False, indent=2))
    output_lines.append("")

    if not stream_result.done:
        errors.append("Stream did not complete with done event")
    if stream_result.errors:
        errors.extend(stream_result.errors)
    if len(stream_result.sequences) == 0:
        errors.append("No messages received")

    if len(stream_result.sequences) > 0:
        for i in range(1, len(stream_result.sequences)):
            if stream_result.sequences[i] <= stream_result.sequences[i-1]:
                errors.append(f"Sequence not strictly increasing: {stream_result.sequences[i-1]} -> {stream_result.sequences[i]}")
                break

    print(f"\n{Colors.CYAN}[Step 4] Waiting for completion...{Colors.ENDC}")
    final_state = await wait_for_conversation_state(api, conversation_id, "completed")
    final_data = final_state.get("data") or {}
    actual_state = final_data.get("state", "unknown")
    if actual_state != "completed":
        errors.append(f"Conversation did not complete, state={actual_state}")
        print(f"{Colors.RED}    Conversation state: {actual_state}{Colors.ENDC}")
    else:
        print(f"{Colors.GREEN}    Conversation completed{Colors.ENDC}")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "conversation_id": conversation_id,
        "last_seq": stream_result.last_seq,
        "message_count": len(stream_result.messages),
    }


async def test_resume_from_breakpoint(api: APIClient, output_file: str) -> Dict:
    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test 2: Resume from Breakpoint{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    errors: List[str] = []
    output_lines: List[str] = []

    output_lines.append("# Test 2: Resume from Breakpoint")
    output_lines.append(f"- timestamp: {get_timestamp()}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("MQ Resume Test")
    if session_result.get("code") != 200:
        error_msg = f"Session creation failed: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    session_id = session_result["data"]["id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    output_lines.append(f"- session_id: {session_id}")

    print(f"{Colors.CYAN}[Step 2] Creating conversation...{Colors.ENDC}")
    conv_result = await api.create_conversation(
        session_id,
        "请详细介绍一下Python的装饰器，包括原理、用法和最佳实践，至少300字。"
    )
    if conv_result.get("code") != 200:
        error_msg = f"Conversation creation failed: {conv_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    conversation_id = conv_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")
    output_lines.append(f"- conversation_id: {conversation_id}")

    print(f"{Colors.CYAN}[Step 3] Receiving first part of stream (max 5 messages)...{Colors.ENDC}\n")
    first_part = await collect_stream_with_seq(api, conversation_id, max_messages=5, verbose=True)
    first_last_seq = first_part.last_seq
    first_messages = first_part.messages.copy()

    output_lines.append("## First Part Result")
    output_lines.append(f"- first_last_seq: {first_last_seq}")
    output_lines.append(f"- first_message_count: {len(first_messages)}")
    output_lines.append("")

    print(f"\n{Colors.YELLOW}[Step 4] Simulating disconnect at seq={first_last_seq}{Colors.ENDC}")
    await asyncio.sleep(0.5)

    print(f"{Colors.CYAN}[Step 5] Resuming from seq={first_last_seq}...{Colors.ENDC}\n")
    resumed_part = await collect_stream_with_seq(api, conversation_id, last_seq=first_last_seq, verbose=True)

    output_lines.append("## Resumed Part Result")
    output_lines.append(json.dumps(resumed_part.to_dict(), ensure_ascii=False, indent=2))
    output_lines.append("")

    if resumed_part.errors:
        errors.extend(resumed_part.errors)

    if len(resumed_part.sequences) > 0:
        for seq in resumed_part.sequences:
            if seq <= first_last_seq:
                errors.append(f"Resumed sequence {seq} is not greater than last_seq {first_last_seq}")

    print(f"\n{Colors.CYAN}[Step 6] Waiting for completion...{Colors.ENDC}")
    final_state = await wait_for_conversation_state(api, conversation_id, "completed")
    print(f"{Colors.GREEN}    Conversation completed{Colors.ENDC}")

    print(f"{Colors.CYAN}[Step 7] Verifying no duplicate messages...{Colors.ENDC}")
    first_contents = [m.get("content", "") for m in first_messages if m.get("type") in ("chat", "text")]
    resumed_contents = [m.get("content", "") for m in resumed_part.messages if m.get("type") in ("chat", "text")]

    overlap = set(first_contents) & set(resumed_contents)
    if overlap:
        errors.append(f"Found duplicate messages after resume: {len(overlap)} items")

    print(f"{Colors.GREEN}    First part: {len(first_messages)} messages, last_seq={first_last_seq}{Colors.ENDC}")
    print(f"{Colors.GREEN}    Resumed part: {len(resumed_part.messages)} messages{Colors.ENDC}")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "conversation_id": conversation_id,
        "first_last_seq": first_last_seq,
        "first_message_count": len(first_messages),
        "resumed_message_count": len(resumed_part.messages),
    }


async def test_completed_conversation_reconnect(api: APIClient, output_file: str) -> Dict:
    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test 3: Reconnect to Completed Conversation{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    errors: List[str] = []
    output_lines: List[str] = []

    output_lines.append("# Test 3: Reconnect to Completed Conversation")
    output_lines.append(f"- timestamp: {get_timestamp()}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("MQ Completed Reconnect Test")
    if session_result.get("code") != 200:
        error_msg = f"Session creation failed: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    session_id = session_result["data"]["id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    output_lines.append(f"- session_id: {session_id}")

    print(f"{Colors.CYAN}[Step 2] Creating conversation...{Colors.ENDC}")
    conv_result = await api.create_conversation(session_id, "你好")
    if conv_result.get("code") != 200:
        error_msg = f"Conversation creation failed: {conv_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    conversation_id = conv_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")
    output_lines.append(f"- conversation_id: {conversation_id}")

    print(f"{Colors.CYAN}[Step 3] Completing first stream...{Colors.ENDC}\n")
    first_stream = await collect_stream_with_seq(api, conversation_id, verbose=True)
    total_seq = first_stream.last_seq

    output_lines.append("## First Stream Result")
    output_lines.append(f"- total_seq: {total_seq}")
    output_lines.append(f"- message_count: {len(first_stream.messages)}")
    output_lines.append("")

    if not first_stream.done:
        errors.append("First stream did not complete")

    print(f"\n{Colors.CYAN}[Step 4] Waiting for completion...{Colors.ENDC}")
    final_state = await wait_for_conversation_state(api, conversation_id, "completed")
    final_data = final_state.get("data") or {}
    actual_state = final_data.get("state", "unknown")
    if actual_state != "completed":
        errors.append(f"Conversation did not complete, state={actual_state}")
        print(f"{Colors.RED}    Conversation state: {actual_state}{Colors.ENDC}")
    else:
        print(f"{Colors.GREEN}    Conversation completed{Colors.ENDC}")

    print(f"{Colors.CYAN}[Step 5] Reconnecting to completed conversation...{Colors.ENDC}\n")
    reconnect_stream = await collect_stream_with_seq(api, conversation_id, last_seq=0, verbose=True)

    output_lines.append("## Reconnect Stream Result")
    output_lines.append(json.dumps(reconnect_stream.to_dict(), ensure_ascii=False, indent=2))
    output_lines.append("")

    if reconnect_stream.errors:
        errors.extend(reconnect_stream.errors)

    print(f"{Colors.GREEN}    Reconnect received {len(reconnect_stream.messages)} messages{Colors.ENDC}")
    print(f"{Colors.GREEN}    Reconnect done={reconnect_stream.done}{Colors.ENDC}")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "conversation_id": conversation_id,
        "total_seq": total_seq,
        "reconnect_message_count": len(reconnect_stream.messages),
    }


async def test_concurrent_subscribers(api: APIClient, output_file: str) -> Dict:
    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test 4: Concurrent Subscribers{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    errors: List[str] = []
    output_lines: List[str] = []

    output_lines.append("# Test 4: Concurrent Subscribers")
    output_lines.append(f"- timestamp: {get_timestamp()}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("MQ Concurrent Test")
    if session_result.get("code") != 200:
        error_msg = f"Session creation failed: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    session_id = session_result["data"]["id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    output_lines.append(f"- session_id: {session_id}")

    print(f"{Colors.CYAN}[Step 2] Creating conversation...{Colors.ENDC}")
    conv_result = await api.create_conversation(
        session_id,
        "请用50字介绍一下Python。"
    )
    if conv_result.get("code") != 200:
        error_msg = f"Conversation creation failed: {conv_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    conversation_id = conv_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")
    output_lines.append(f"- conversation_id: {conversation_id}")

    print(f"{Colors.CYAN}[Step 3] Starting 3 concurrent subscribers...{Colors.ENDC}\n")

    async def collect_with_id(subscriber_id: int):
        result = await collect_stream_with_seq(
            api, conversation_id, verbose=False
        )
        return {"id": subscriber_id, "result": result}

    tasks = [
        asyncio.create_task(collect_with_id(i))
        for i in range(3)
    ]

    results = await asyncio.gather(*tasks)

    output_lines.append("## Subscriber Results")
    for r in results:
        subscriber_id = r["id"]
        result = r["result"]
        output_lines.append(f"### Subscriber {subscriber_id}")
        output_lines.append(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        output_lines.append("")

        if result.errors:
            errors.extend([f"Subscriber {subscriber_id}: {e}" for e in result.errors])
        if not result.done:
            errors.append(f"Subscriber {subscriber_id} did not complete")

    print(f"\n{Colors.CYAN}[Step 4] Waiting for completion...{Colors.ENDC}")
    final_state = await wait_for_conversation_state(api, conversation_id, "completed")
    final_data = final_state.get("data") or {}
    actual_state = final_data.get("state", "unknown")
    if actual_state != "completed":
        print(f"{Colors.YELLOW}    Conversation state: {actual_state}{Colors.ENDC}")
    else:
        print(f"{Colors.GREEN}    Conversation completed{Colors.ENDC}")

    print(f"{Colors.CYAN}[Step 5] Verifying all subscribers received messages...{Colors.ENDC}")
    for r in results:
        subscriber_id = r["id"]
        result = r["result"]
        msg_count = len(result.messages)
        print(f"{Colors.GREEN}    Subscriber {subscriber_id}: {msg_count} messages{Colors.ENDC}")
        if msg_count == 0:
            errors.append(f"Subscriber {subscriber_id} received no messages")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "conversation_id": conversation_id,
        "subscriber_results": [
            {"id": r["id"], "message_count": len(r["result"].messages)}
            for r in results
        ],
    }


async def run_all_tests(api: APIClient, output_dir: Path):
    results: List[Dict] = []

    test1_result = await test_normal_stream(
        api,
        str(output_dir / "test1_normal_stream.md")
    )
    results.append({"test": "normal_stream", **test1_result})

    test2_result = await test_resume_from_breakpoint(
        api,
        str(output_dir / "test2_resume_breakpoint.md")
    )
    results.append({"test": "resume_breakpoint", **test2_result})

    test3_result = await test_completed_conversation_reconnect(
        api,
        str(output_dir / "test3_completed_reconnect.md")
    )
    results.append({"test": "completed_reconnect", **test3_result})

    test4_result = await test_concurrent_subscribers(
        api,
        str(output_dir / "test4_concurrent_subscribers.md")
    )
    results.append({"test": "concurrent_subscribers", **test4_result})

    return results


def print_summary(results: List[Dict]):
    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test Summary{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    all_passed = True
    for r in results:
        test_name = r.get("test", "unknown")
        success = r.get("success", False)
        errors = r.get("errors", [])

        status = f"{Colors.GREEN}PASSED{Colors.ENDC}" if success else f"{Colors.RED}FAILED{Colors.ENDC}"
        print(f"  {test_name}: {status}")

        if errors:
            for e in errors:
                print(f"    {Colors.RED}- {e}{Colors.ENDC}")

        if not success:
            all_passed = False

    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    if all_passed:
        print(f"{Colors.GREEN}  All tests passed!{Colors.ENDC}")
    else:
        print(f"{Colors.RED}  Some tests failed!{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    return all_passed


async def main(no_server: bool = False):
    output_dir = Path(__file__).parent / "output" / f"mq_resume_e2e_{get_timestamp()}"
    output_dir.mkdir(parents=True, exist_ok=True)

    backend_process = None

    if not no_server:
        if not wait_for_backend():
            backend_process = start_backend()
            print(f"{Colors.CYAN}Backend process started, waiting for ready...{Colors.ENDC}")
            if not wait_for_backend(timeout=60.0):
                print(f"{Colors.RED}Failed to start backend{Colors.ENDC}")
                return 1

    try:
        api = APIClient(BASE_URL, user_id=1)
        results = await run_all_tests(api, output_dir)

        all_passed = print_summary(results)

        summary_file = output_dir / "summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"{Colors.CYAN}Output directory: {output_dir}{Colors.ENDC}")

        return 0 if all_passed else 1

    finally:
        if backend_process:
            stop_backend(backend_process)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Message Queue E2E Test")
    parser.add_argument("--no-server", action="store_true", help="Do not start server")
    args = parser.parse_args()

    exit_code = asyncio.run(main(no_server=args.no_server))
    sys.exit(exit_code)
