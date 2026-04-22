#!/usr/bin/env python3
"""
E2E Test Base Module

提供测试框架的基础类和工具函数
"""

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
from typing import Dict, List, Optional, Any
from urllib.error import URLError
from urllib.request import urlopen

import httpx
import yaml


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


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sanitized = text.encode(encoding, errors="replace").decode(encoding)
        print(sanitized)


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Optional[str] = None) -> Dict:
    if config_path is None:
        config_path = Path(__file__).parent.parent / "test_config.yaml"
    else:
        config_path = Path(config_path)
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    backend_dir = Path(__file__).parent.parent.resolve()
    python_executable = sys.executable

    run_server = backend_dir / "run_server.py"
    command = [
        python_executable,
        str(run_server),
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


class TestResult:
    def __init__(self, scenario: str, config: Dict):
        self.scenario = scenario
        self.config = config
        self.event_count = 0
        self.thinking_content = ""
        self.chat_content = ""
        self.text_content = ""
        self.tool_calls: List[str] = []
        self.errors: List[str] = []
        self.plan_status: Optional[str] = None
        self.workspace_id: Optional[str] = None
        self.conversation_id: Optional[str] = None
        self.session_id: Optional[int] = None
        self.detected_mode: Optional[str] = None
        self.detected_modes: List[str] = []
        self.raw_lines: List[str] = []
        self.done = False
        self.response_text = ""

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "description": self.config.get("description"),
            "event_count": self.event_count,
            "thinking_length": len(self.thinking_content),
            "chat_length": len(self.chat_content),
            "text_length": len(self.text_content),
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "plan_status": self.plan_status,
            "workspace_id": self.workspace_id,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "detected_mode": self.detected_mode,
            "detected_modes": self.detected_modes,
            "done": self.done,
            "response_text": self.response_text[:500] if self.response_text else None,
        }


class APIClient:
    def __init__(self, config: Dict, user_id: int = 1):
        self.config = config
        api_config = config.get("api", {})
        self.base_url = api_config.get("base_url", "http://localhost:8000").rstrip("/")
        self.endpoints = api_config.get("endpoints", {})
        self.user_id = user_id
        timeout_config = api_config.get("timeout", {})
        self.timeout = httpx.Timeout(
            connect=timeout_config.get("connect", 30.0),
            read=timeout_config.get("read"),
            write=timeout_config.get("write", 300.0),
            pool=timeout_config.get("pool", 300.0),
        )

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-User-ID": str(self.user_id),
        }

    def _auth_headers(self) -> dict:
        return {
            "X-User-ID": str(self.user_id),
        }

    def _get_endpoint(self, category: str, name: str, **kwargs) -> str:
        endpoints = self.endpoints.get(category, {})
        path = endpoints.get(name, "")
        for key, value in kwargs.items():
            path = path.replace(f"{{{key}}}", str(value))
        return path

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
                            "success": False,
                        }
                    api_code = data.get("code")
                    if api_code is not None and api_code != 200:
                        return {
                            "code": api_code,
                            "message": data.get("message", "Unknown error"),
                            "data": data.get("data"),
                            "success": False,
                        }
                    return {"success": True, **data}
                except Exception:
                    return {"code": response.status_code, "message": response.text, "data": None, "success": False}
            except Exception as e:
                return {"code": -1, "message": str(e), "data": None, "success": False}

    async def create_session(self, title: str = "Test Session") -> dict:
        path = self._get_endpoint("session", "create")
        return await self._request("POST", path, json={"title": title})

    async def get_session(self, session_id: int) -> dict:
        path = self._get_endpoint("session", "get", session_id=session_id)
        return await self._request("GET", path)

    async def list_sessions(self) -> dict:
        path = self._get_endpoint("session", "list")
        return await self._request("GET", path)

    async def generate_session_title(self, session_id: int) -> dict:
        path = self._get_endpoint("session", "generate_title", session_id=session_id)
        return await self._request("POST", path)

    async def create_conversation(self, session_id: int, user_content: str, user_content_parts: Optional[List[dict]] = None) -> dict:
        path = self._get_endpoint("conversation", "create", session_id=session_id)
        if user_content_parts:
            body = {"user_content_parts": user_content_parts}
        else:
            body = {"user_content": user_content}
        return await self._request("POST", path, json=body)

    async def get_conversation(self, conversation_id: str) -> dict:
        path = self._get_endpoint("conversation", "get", conversation_id=conversation_id)
        return await self._request("GET", path)

    async def get_plan_status(self, workspace_id: str) -> dict:
        path = self._get_endpoint("plan", "status", workspace_id=workspace_id)
        return await self._request("GET", path)

    async def approve_plan(self, workspace_id: str, approved: bool = True) -> dict:
        path = self._get_endpoint("plan", "approve")
        return await self._request(
            "POST", path,
            json={"workspace_id": workspace_id, "approved": approved}
        )

    async def get_workspace(self, workspace_id: str) -> dict:
        path = self._get_endpoint("workspace", "get", workspace_id=workspace_id)
        return await self._request("GET", path)

    async def list_workspace_files(self, workspace_id: str) -> dict:
        path = self._get_endpoint("workspace", "list_files", workspace_id=workspace_id)
        return await self._request("GET", path)

    async def upload_workspace_file(self, workspace_id: str, file_path: Path) -> dict:
        path = self._get_endpoint("workspace", "upload_file", workspace_id=workspace_id)
        
        mime_types = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pdf": "application/pdf",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".txt": "text/plain; charset=utf-8",
        }
        
        suffix = file_path.suffix.lower()
        mime_type = mime_types.get(suffix, "application/octet-stream")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(file_path, "rb") as f:
                    response = await client.post(
                        f"{self.base_url}{path}",
                        headers=self._auth_headers(),
                        files=[("files", (file_path.name, f, mime_type))],
                    )
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

    async def stream_message(self, conversation_id: str, last_seq: int = 0, use_v2: bool = False):
        if use_v2:
            path = self._get_endpoint("conversation", "stream_v2", conversation_id=conversation_id)
        else:
            path = self._get_endpoint("conversation", "stream", conversation_id=conversation_id)
            path = f"{path}?last_seq={last_seq}"
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            method = "POST" if use_v2 else "GET"
            async with client.stream(method, f"{self.base_url}{path}", headers=self._headers()) as response:
                if response.status_code != 200:
                    try:
                        error = await response.aread()
                        yield {"type": "error", "raw": error.decode(), "status_code": response.status_code}
                    except Exception as e:
                        yield {"type": "error", "raw": str(e), "status_code": response.status_code}
                    return

                async for line in response.aiter_lines():
                    yield {"raw_line": line}


async def wait_for_conversation_state(
    api: APIClient,
    conversation_id: str,
    expected_state: str,
    timeout: float = 120.0,
) -> dict:
    deadline = time.time() + timeout
    last_result = None
    while time.time() < deadline:
        conversation_result = await api.get_conversation(conversation_id)
        last_result = conversation_result
        data = conversation_result.get("data") or {}
        current_state = data.get("state")
        if current_state == expected_state:
            return conversation_result
        if expected_state == "processing" and current_state in ("running", "pending"):
            return conversation_result
        if expected_state == "completed" and current_state in ("running", "pending"):
            pass
        await asyncio.sleep(0.5)
    return last_result


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


async def collect_stream_output(
    api: APIClient,
    conversation_id: str,
    result: TestResult,
    verbose: bool = True,
    show_raw: bool = False,
    use_v2: bool = False,
    timeout: float = 60.0,
):
    import asyncio
    
    deadline = time.time() + timeout
    stream_iter = api.stream_message(conversation_id, use_v2=use_v2)
    
    while time.time() < deadline:
        try:
            item = await asyncio.wait_for(stream_iter.__anext__(), timeout=1.0)
        except asyncio.TimeoutError:
            if result.done:
                break
            continue
        except StopAsyncIteration:
            break
        
        raw_line = item.get("raw_line", "")
        if not raw_line.strip():
            continue

        if show_raw:
            result.raw_lines.append(raw_line)
            timestamp = time.strftime("%H:%M:%S")
            print(f"{Colors.DIM}[RAW {timestamp}] {raw_line}{Colors.ENDC}")

        if raw_line.startswith(": heartbeat"):
            if verbose:
                print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
            continue

        if not raw_line.startswith("data: "):
            continue

        try:
            data = json.loads(raw_line[6:])
        except json.JSONDecodeError:
            continue

        event_type = data.get("type", "unknown")
        result.event_count += 1

        if event_type == "text_delta":
            content = data.get("content", "")
            result.text_content += content
            if verbose:
                safe_print(f"{Colors.CYAN}[text] {content}{Colors.ENDC}")
        elif event_type == "chat_delta":
            content = data.get("content", "")
            result.chat_content += content
            if verbose:
                safe_print(f"{Colors.GREEN}[chat] {content}{Colors.ENDC}")
        elif event_type == "chat_end":
            if verbose:
                print(f"{Colors.GREEN}[chat_end] Chat completed{Colors.ENDC}")
        elif event_type == "thinking_delta":
            content = data.get("content", "")
            result.thinking_content += content
            if verbose:
                safe_print(f"{Colors.DIM}[thinking] {content[:50]}...{Colors.ENDC}")
        elif event_type == "tool_call":
            metadata = data.get("metadata", {})
            tool_name = metadata.get("tool_name", "unknown")
            result.tool_calls.append(tool_name)
            if verbose:
                print(f"{Colors.MAGENTA}[tool_call] {tool_name}{Colors.ENDC}")
        elif event_type == "state_change":
            metadata = data.get("metadata", {})
            execution_mode = metadata.get("execution_mode")
            if execution_mode:
                result.detected_mode = execution_mode
                if execution_mode not in result.detected_modes:
                    result.detected_modes.append(execution_mode)
                if verbose:
                    print(f"{Colors.YELLOW}[state] execution_mode: {execution_mode}{Colors.ENDC}")
            plan_status = metadata.get("plan_status")
            if plan_status:
                result.plan_status = plan_status
                if verbose:
                    print(f"{Colors.YELLOW}[state] plan_status: {plan_status}{Colors.ENDC}")
        elif event_type == "plan_start":
            if verbose:
                print(f"{Colors.YELLOW}[plan_start] Plan generation started{Colors.ENDC}")
        elif event_type == "plan_delta":
            content = data.get("content", "")
            if verbose:
                print(f"{Colors.YELLOW}[plan] {content[:50]}...{Colors.ENDC}")
        elif event_type == "plan_end":
            if verbose:
                print(f"{Colors.YELLOW}[plan_end] Plan generation completed{Colors.ENDC}")
        elif event_type == "conversation_handoff":
            metadata = data.get("metadata", {})
            if verbose:
                print(f"{Colors.HEADER}[conversation_handoff] auto_approved: {metadata.get('auto_approved')}, next_conversation_id: {metadata.get('next_conversation_id')}{Colors.ENDC}")
        elif event_type == "done":
            result.done = True
            if verbose:
                print(f"{Colors.GREEN}[done] Stream completed{Colors.ENDC}")
            break
        elif event_type == "error":
            error_content = data.get("content", "Unknown error")
            result.errors.append(error_content)
            if verbose:
                safe_print(f"{Colors.RED}[error] {error_content}{Colors.ENDC}")
        else:
            if verbose:
                print(f"{Colors.BLUE}[{event_type}] {json.dumps(data, ensure_ascii=False)[:100]}...{Colors.ENDC}")


def print_test_header(description: str):
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  {description}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")


def print_step(step: int, message: str, color: str = Colors.CYAN):
    print(f"{color}[Step {step}] {message}{Colors.ENDC}")


def print_success(message: str):
    print(f"{Colors.GREEN}    {message}{Colors.ENDC}")


def print_error(message: str):
    print(f"{Colors.RED}    {message}{Colors.ENDC}")


def print_dim(message: str):
    print(f"{Colors.DIM}    {message}{Colors.ENDC}")
