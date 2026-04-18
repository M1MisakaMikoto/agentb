#!/usr/bin/env python3
"""
Simple E2E Test - Multi-Mode Agent Testing with Raw Stream Output

Tests different execution modes:
- DIRECT: Simple tasks (e.g., "你好")
- PLAN: Complex development tasks (e.g., "帮我实现一个用户登录功能")
- SEARCH: Search tasks that still stay in DIRECT mode

Usage:
    python simple_e2e_stream.py [--no-server] [--mode direct|plan|search|serial|all]
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
from typing import Optional, Dict, List
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


def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sanitized = text.encode(encoding, errors="replace").decode(encoding)
        print(sanitized)


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


TEST_CASES: Dict[str, Dict[str, any]] = {
    "direct": {
        "question": "你好，请简单介绍一下你自己",
        "description": "DIRECT 模式 - 简单对话任务",
        "expected_mode": "DIRECT",
        "expected_tools": ["thinking", "chat"],
        "forbidden_tools": ["update_todo"],
        "forbidden_reply_terms": ["ReAct", "todo", "工具", "执行代理", "状态机", "计划文件"],
    },
    "plan": {
        "question": "请把“实现一个简单的用户登录功能（包含前端表单、后端验证、会话管理和错误提示）”当作复杂多阶段开发任务处理。先生成计划文件，然后只读取当前工作区文件结构，说明如果后续真的实现它，应该优先查看哪些文件。不要修改任何文件，不要创建新文件，也不要真正实现登录功能。",
        "description": "PLAN 模式 - 先规划再轻量执行",
        "expected_modes": ["PLAN", "DIRECT"],
        "forbidden_tools": ["write_file", "delete_file", "create_dir"],
        "stop_when_expected_modes_seen": True,
    },
    "search": {
        "question": "请使用 explore_internet 工具搜索市政设施管理规定的相关法规",
        "description": "SEARCH 模式 - 网络搜索与文件查看",
        "expected_mode": "DIRECT",
        "expected_tools": ["explore_internet"],
    },
    "serial": {
        "question": "请记住暗号 ALPHA-9271，只回复这串暗号。",
        "description": "SERIAL 模式 - 同一 Session 串行对话约束与历史继承测试",
        "expected_mode": "DIRECT",
        "expected_tools": ["thinking", "chat"],
    },
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
                except Exception:
                    return {"code": response.status_code, "message": response.text, "data": None}
            except Exception as e:
                return {"code": -1, "message": str(e), "data": None}

    async def create_session(self, title: str = "Test Session") -> dict:
        return await self._request("POST", "/session/sessions", json={"title": title})

    async def create_conversation(self, session_id: int, user_content: str) -> dict:
        return await self._request(
            "POST", f"/session/sessions/{session_id}/conversations", json={"user_content": user_content}
        )

    async def get_plan_status(self, workspace_id: str) -> dict:
        return await self._request("GET", f"/plan/{workspace_id}/status")

    async def approve_plan(self, workspace_id: str, approved: bool = True) -> dict:
        return await self._request(
            "POST", "/plan/approve",
            json={"workspace_id": workspace_id, "approved": approved}
        )

    async def get_conversation(self, conversation_id: str) -> dict:
        return await self._request("GET", f"/session/conversations/{conversation_id}")

    async def stream_message(self, conversation_id: str):
        url = f"{self.base_url}/session/conversations/{conversation_id}/messages/stream"
        headers = self._headers()

        timeout = httpx.Timeout(connect=30.0, read=None, write=300.0, pool=300.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers) as response:
                if response.status_code != 200:
                    try:
                        error = await response.aread()
                        yield {"type": "error", "raw": error.decode(), "status_code": response.status_code}
                    except Exception as e:
                        yield {"type": "error", "raw": str(e), "status_code": response.status_code}
                    return

                async for line in response.aiter_lines():
                    yield {"raw_line": line}


class TestResult:
    def __init__(self, mode: str, test_case: Dict):
        self.mode = mode
        self.test_case = test_case
        self.event_count = 0
        self.thinking_content = ""
        self.chat_content = ""
        self.text_content = ""
        self.tool_calls: List[str] = []
        self.errors: List[str] = []
        self.plan_status: Optional[str] = None
        self.workspace_id: Optional[str] = None
        self.conversation_id: Optional[str] = None
        self.detected_mode: Optional[str] = None
        self.detected_modes: List[str] = []
        self.raw_lines: List[str] = []

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "description": self.test_case.get("description"),
            "question": self.test_case.get("question"),
            "expected_mode": self.test_case.get("expected_mode"),
            "detected_mode": self.detected_mode,
            "event_count": self.event_count,
            "thinking_length": len(self.thinking_content),
            "chat_length": len(self.chat_content),
            "text_length": len(self.text_content),
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "plan_status": self.plan_status,
            "workspace_id": self.workspace_id,
            "conversation_id": self.conversation_id,
        }


async def collect_stream_output(api: APIClient, conversation_id: str):
    event_types: List[str] = []
    text_chunks: List[str] = []
    chat_chunks: List[str] = []
    errors: List[str] = []
    done = False

    async for item in api.stream_message(conversation_id):
        raw_line = item.get("raw_line", "")
        if not raw_line.strip() or not raw_line.startswith("data: "):
            continue

        try:
            data = json.loads(raw_line[6:])
        except json.JSONDecodeError:
            continue

        event_type = data.get("type", "unknown")
        event_types.append(event_type)

        if event_type == "text_delta":
            text_chunks.append(data.get("content", ""))
        elif event_type == "chat_delta":
            chat_chunks.append(data.get("content", ""))
        elif event_type == "error":
            errors.append(data.get("content", "Unknown error"))
        elif event_type == "done":
            done = True

    return {
        "event_types": event_types,
        "text": "".join(text_chunks),
        "chat": "".join(chat_chunks),
        "errors": errors,
        "done": done,
    }


async def wait_for_conversation_state(api: APIClient, conversation_id: str, expected_state: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        conversation_result = await api.get_conversation(conversation_id)
        data = conversation_result.get("data") or {}
        if data.get("state") == expected_state:
            return conversation_result
        await asyncio.sleep(0.1)
    return conversation_result


def extract_response_text(conversation_result: dict) -> str:
    data = conversation_result.get("data") or {}
    assistant_content = data.get("assistant_content")
    if not assistant_content:
        return ""
    try:
        events = json.loads(assistant_content)
    except Exception:
        return str(assistant_content)

    parts: List[str] = []
    for event in events:
        event_type = event.get("type")
        if event_type in {"text_delta", "chat_delta", "thinking_delta"}:
            parts.append(event.get("content", ""))
        elif event_type == "thinking_end":
            metadata = event.get("metadata") or {}
            if metadata.get("result"):
                parts.append(metadata["result"])
    return "".join(parts)


async def run_serial_test(api: APIClient, output_file: str) -> TestResult:
    test_case = TEST_CASES["serial"]
    result = TestResult("serial", test_case)

    session_result = await api.create_session("Test SERIAL")
    if session_result.get("code") != 200:
        result.errors.append(f"Session creation failed: {session_result.get('message', 'Unknown error')}")
        return result

    session_id = session_result["data"]["id"]

    first_prompt = test_case["question"]
    first_conv_result = await api.create_conversation(session_id, first_prompt)
    if first_conv_result.get("code") != 200:
        result.errors.append(f"First conversation creation failed: {first_conv_result.get('message', 'Unknown error')}")
        return result

    first_conversation_id = first_conv_result["data"]["conversation_id"]
    result.conversation_id = first_conversation_id

    first_stream_task = asyncio.create_task(collect_stream_output(api, first_conversation_id))
    await wait_for_conversation_state(api, first_conversation_id, "running")

    blocked_conv_result = await api.create_conversation(
        session_id,
        "上一轮会话中的暗号是什么？只回答暗号。",
    )
    if blocked_conv_result.get("code") != 409:
        result.errors.append(f"Expected 409 while first conversation is running, got: {blocked_conv_result}")

    blocked_message = blocked_conv_result.get("message", "")
    if "正在执行" not in blocked_message:
        result.errors.append(f"Unexpected conflict message: {blocked_message}")

    first_stream_result = await first_stream_task
    result.event_count += len(first_stream_result["event_types"])
    if first_stream_result["errors"]:
        result.errors.extend(first_stream_result["errors"])
    if not first_stream_result["done"]:
        result.errors.append("First conversation stream did not complete with done event")

    first_conversation_state = await wait_for_conversation_state(api, first_conversation_id, "completed")
    first_response = extract_response_text(first_conversation_state)
    if "ALPHA-9271" not in first_response:
        result.errors.append(f"First conversation did not return the code word: {first_response}")

    second_conv_result = await api.create_conversation(
        session_id,
        "上一轮会话中的暗号是什么？只回答暗号。",
    )
    if second_conv_result.get("code") != 200:
        result.errors.append(f"Second conversation creation failed: {second_conv_result.get('message', 'Unknown error')}")
        return result

    second_conversation_id = second_conv_result["data"]["conversation_id"]
    second_stream_result = await collect_stream_output(api, second_conversation_id)
    result.event_count += len(second_stream_result["event_types"])
    if second_stream_result["errors"]:
        result.errors.extend(second_stream_result["errors"])
    if not second_stream_result["done"]:
        result.errors.append("Second conversation stream did not complete with done event")

    second_conversation_state = await wait_for_conversation_state(api, second_conversation_id, "completed")
    second_response = extract_response_text(second_conversation_state)
    if "ALPHA-9271" not in second_response:
        result.errors.append(f"Second conversation did not inherit previous history: {second_response}")

    with open(output_file, "a", encoding="utf-8") as f:
        f.write("# SERIAL Session Conversation Test\n")
        f.write(f"- session_id: {session_id}\n")
        f.write(f"- first_conversation_id: {first_conversation_id}\n")
        f.write(f"- blocked_create_result: {json.dumps(blocked_conv_result, ensure_ascii=False)}\n")
        f.write(f"- second_conversation_id: {second_conversation_id}\n")
        f.write(f"- first_response: {first_response}\n")
        f.write(f"- second_response: {second_response}\n\n")

    result.text_content = second_response
    result.detected_mode = "DIRECT"
    return result


async def run_single_test(
    api: APIClient,
    mode: str,
    test_case: Dict,
    output_file: str,
    auto_approve_plan: bool = True,
) -> TestResult:
    result = TestResult(mode, test_case)
    question = test_case["question"]

    raw_output_lines = []

    def log_raw(line: str):
        raw_output_lines.append(line)
        result.raw_lines.append(line)

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  {test_case.get('description')}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    log_raw(f"# {test_case.get('description')}")
    log_raw(f"# Timestamp: {get_timestamp()}")
    log_raw(f"# Question: {question}")
    log_raw("")

    print(f"{Colors.CYAN}[1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session(f"Test {mode.upper()}")
    if session_result.get("code") != 200:
        error_msg = session_result.get("message", "Unknown error")
        print(f"{Colors.RED}Failed: {error_msg}{Colors.ENDC}")
        result.errors.append(f"Session creation failed: {error_msg}")
        return result

    session_id = session_result["data"]["id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    log_raw(f"## Session ID: {session_id}")

    print(f"{Colors.CYAN}[2] Creating conversation...{Colors.ENDC}")
    print(f"{Colors.DIM}    Question: {question}{Colors.ENDC}")
    conv_result = await api.create_conversation(session_id, question)
    if conv_result.get("code") != 200:
        error_msg = conv_result.get("message", "Unknown error")
        print(f"{Colors.RED}Failed: {error_msg}{Colors.ENDC}")
        result.errors.append(f"Conversation creation failed: {error_msg}")
        return result

    conversation_id = conv_result["data"]["conversation_id"]
    result.conversation_id = conversation_id
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")
    log_raw(f"## Conversation ID: {conversation_id}")

    workspace_id = conv_result["data"].get("workspace_id")
    if workspace_id:
        result.workspace_id = workspace_id
        log_raw(f"## Workspace ID: {workspace_id}")

    log_raw("")
    log_raw("## Raw Stream Data")
    log_raw("```")

    print(f"\n{Colors.CYAN}[3] Receiving stream...{Colors.ENDC}\n")

    async for item in api.stream_message(conversation_id):
        raw_line = item.get("raw_line", "")

        if not raw_line.strip():
            continue

        log_raw(raw_line)

        if raw_line.startswith(": heartbeat"):
            print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
            continue

        if raw_line.startswith("data: "):
            result.event_count += 1
            json_str = raw_line[6:]

            try:
                data = json.loads(json_str)
                event_type = data.get("type", "unknown")

                if event_type == "thinking_delta":
                    content = data.get("content", "")
                    result.thinking_content += content
                    safe_print(f"{Colors.DIM}[thinking] {content[:50]}...{Colors.ENDC}")

                elif event_type == "chat_delta":
                    content = data.get("content", "")
                    result.chat_content += content
                    safe_print(f"{Colors.GREEN}[chat] {content}{Colors.ENDC}")

                elif event_type == "text_delta":
                    content = data.get("content", "")
                    result.text_content += content
                    safe_print(f"{Colors.CYAN}[text] {content}{Colors.ENDC}")

                elif event_type == "tool_call":
                    metadata = data.get("metadata", {})
                    tool_name = metadata.get("tool_name", "unknown")
                    result.tool_calls.append(tool_name)
                    print(f"{Colors.MAGENTA}[tool_call] {tool_name}{Colors.ENDC}")

                elif event_type == "state_change":
                    metadata = data.get("metadata", {})
                    execution_mode = metadata.get("execution_mode")
                    if execution_mode:
                        result.detected_mode = execution_mode
                        if execution_mode not in result.detected_modes:
                            result.detected_modes.append(execution_mode)
                        print(f"{Colors.YELLOW}[state] execution_mode: {execution_mode}{Colors.ENDC}")
                        expected_modes = test_case.get("expected_modes") or []
                        if test_case.get("stop_when_expected_modes_seen") and expected_modes and all(mode in result.detected_modes for mode in expected_modes):
                            print(f"{Colors.YELLOW}[state] expected mode chain observed, stopping stream collection early{Colors.ENDC}")
                            break
                    plan_status = metadata.get("plan_status")
                    if plan_status:
                        result.plan_status = plan_status
                        print(f"{Colors.YELLOW}[state] plan_status: {plan_status}{Colors.ENDC}")

                elif event_type == "plan_start":
                    print(f"{Colors.YELLOW}[plan_start] Plan generation started{Colors.ENDC}")

                elif event_type == "plan_delta":
                    content = data.get("content", "")
                    print(f"{Colors.YELLOW}[plan] {content[:50]}...{Colors.ENDC}")

                elif event_type == "plan_end":
                    print(f"{Colors.YELLOW}[plan_end] Plan generation completed{Colors.ENDC}")

                elif event_type == "done":
                    print(f"{Colors.GREEN}[done] Stream completed{Colors.ENDC}")

                elif event_type == "error":
                    error_content = data.get("content", "Unknown error")
                    result.errors.append(error_content)
                    safe_print(f"{Colors.RED}[error] {error_content}{Colors.ENDC}")

                else:
                    print(f"{Colors.BLUE}[{event_type}] {json.dumps(data, ensure_ascii=False)[:100]}...{Colors.ENDC}")

            except json.JSONDecodeError as e:
                print(f"{Colors.RED}JSON parse error: {e}{Colors.ENDC}")

    log_raw("```")
    log_raw("")

    if result.plan_status == "waiting_approval" and auto_approve_plan and workspace_id:
        print(f"\n{Colors.YELLOW}[4] Plan approval flow is no longer used in this test path{Colors.ENDC}")

    log_raw("## Test Result Summary")
    summary = result.to_dict()
    for key, value in summary.items():
        log_raw(f"- {key}: {value}")

    forbidden_tools = test_case.get("forbidden_tools", [])
    for forbidden_tool in forbidden_tools:
        if forbidden_tool in result.tool_calls:
            result.errors.append(f"Forbidden tool was used: {forbidden_tool}")

    forbidden_reply_terms = test_case.get("forbidden_reply_terms", [])
    final_visible_reply = (result.chat_content or result.text_content or "").strip()
    for term in forbidden_reply_terms:
        if term and term.lower() in final_visible_reply.lower():
            result.errors.append(f"Forbidden reply term was exposed: {term}")

    expected_tools = test_case.get("expected_tools", [])
    for expected_tool in expected_tools:
        if expected_tool not in result.tool_calls and expected_tool not in {"thinking", "chat"}:
            result.errors.append(f"Expected tool was not used: {expected_tool}")

    with open(output_file, "a", encoding="utf-8") as f:
        f.write("\n".join(raw_output_lines) + "\n\n")

    return result


def print_summary(results: List[TestResult]):
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test Summary{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    print(f"| {'Mode':<10} | {'Expected':<10} | {'Detected':<10} | {'Events':<8} | {'Tools':<30} | {'Status':<10} |")
    print(f"|{'-'*12}|{'-'*12}|{'-'*12}|{'-'*10}|{'-'*32}|{'-'*12}|")

    all_passed = True
    for r in results:
        expected_modes = r.test_case.get("expected_modes")
        if expected_modes:
            mode_match = all(mode in r.detected_modes for mode in expected_modes)
            expected_display = "->".join(expected_modes)
            detected = "->".join(r.detected_modes) if r.detected_modes else (r.detected_mode or "N/A")
        else:
            mode_match = r.detected_mode == r.test_case.get("expected_mode") if r.detected_mode else False
            expected_display = r.test_case.get("expected_mode")
            detected = r.detected_mode or "N/A"
        status = f"{Colors.GREEN}PASS{Colors.ENDC}" if mode_match and not r.errors else f"{Colors.RED}FAIL{Colors.ENDC}"
        if not (mode_match and not r.errors):
            all_passed = False

        tools_str = ", ".join(r.tool_calls[:3]) + ("..." if len(r.tool_calls) > 3 else "")

        print(f"| {r.mode:<10} | {expected_display:<10} | {detected:<10} | {r.event_count:<8} | {tools_str:<30} | {status} |")

        if r.errors:
            print(f"  {Colors.RED}Errors: {r.errors}{Colors.ENDC}")
        if r.chat_content:
            print(f"  {Colors.GREEN}Chat: {r.chat_content[:100]}...{Colors.ENDC}")

    print()
    if all_passed:
        print(f"{Colors.GREEN}All tests passed!{Colors.ENDC}")
    else:
        print(f"{Colors.RED}Some tests failed.{Colors.ENDC}")

    return all_passed


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


async def main():
    parser = argparse.ArgumentParser(description="E2E Stream Test - Multi-Mode Agent Testing")
    parser.add_argument("--no-server", action="store_true", help="Do not start backend server")
    parser.add_argument("--mode", "-m", choices=["direct", "plan", "search", "serial", "all"], default="all",
                        help="Test mode to run (default: all)")
    parser.add_argument("--question", "-q", default=None, help="Custom question (overrides mode)")
    parser.add_argument("--expected-mode", choices=["DIRECT", "PLAN"], default=None,
                        help="Expected execution mode for custom question")
    parser.add_argument("--user-id", "-u", type=int, default=99999, help="User ID")
    parser.add_argument("--output", "-o", default=None, help="Output file path")
    parser.add_argument("--auto-approve", action="store_true", default=True,
                        help="Auto-approve plans in PLAN mode")
    args = parser.parse_args()

    timestamp = get_timestamp()
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    output_file = args.output or str(logs_dir / f"multi_mode_test_{timestamp}.md")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# Multi-Mode E2E Test Report\n")
        f.write(f"# Generated: {timestamp}\n\n")

    backend_process = None
    started_backend = False

    try:
        if args.no_server:
            if not wait_for_backend(timeout=2):
                print(f"{Colors.RED}Backend not running, please start or remove --no-server{Colors.ENDC}")
                return 1
        else:
            if not wait_for_backend(timeout=2):
                print(f"{Colors.CYAN}Initializing database...{Colors.ENDC}")
                backend_dir = Path(__file__).parent.parent
                if str(backend_dir) not in sys.path:
                    sys.path.insert(0, str(backend_dir))
                from singleton import get_mysql_database
                db = await get_mysql_database()
                await db.init_tables()
                print(f"{Colors.GREEN}Database initialized{Colors.ENDC}")

                backend_process = start_backend()
                started_backend = True

                if not wait_for_backend(timeout=120):
                    print(f"{Colors.RED}Cannot start backend{Colors.ENDC}")
                    return 1

        api = APIClient(BASE_URL, args.user_id)
        results: List[TestResult] = []

        if args.question:
            expected_mode = args.expected_mode or "DIRECT"
            custom_case = {
                "question": args.question,
                "description": "Custom question test",
                "expected_mode": expected_mode,
                "expected_tools": ["thinking", "chat"],
            }
            result = await run_single_test(api, "custom", custom_case, output_file, args.auto_approve)
            results.append(result)
        else:
            modes_to_test = ["direct", "plan", "search", "serial"] if args.mode == "all" else [args.mode]

            for mode in modes_to_test:
                if mode == "serial":
                    result = await run_serial_test(api, output_file)
                    results.append(result)
                elif mode in TEST_CASES:
                    result = await run_single_test(
                        api, mode, TEST_CASES[mode], output_file, args.auto_approve
                    )
                    results.append(result)

                await asyncio.sleep(1)

        all_passed = print_summary(results)

        print(f"\n{Colors.CYAN}Detailed output saved to: {output_file}{Colors.ENDC}")

        return 0 if all_passed else 1

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted{Colors.ENDC}")
        return 130
    finally:
        if started_backend and backend_process is not None:
            stop_backend(backend_process)
        try:
            backend_dir = Path(__file__).parent.parent
            if str(backend_dir) not in sys.path:
                sys.path.insert(0, str(backend_dir))
            from singleton import clear_all_singletons_async
            await clear_all_singletons_async()
        except Exception as e:
            print(f"{Colors.YELLOW}Cleanup warning: {e}{Colors.ENDC}")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
