#!/usr/bin/env python3
"""
Cross Lifecycle E2E Test

测试目标:
1. 新建 session，上传文件到 workspace，发送"你好"测试
2. 重启后端
3. 再次上传文件到同一个 workspace
4. 验证 session 可访问、对话历史保留、新文件上传成功

Usage:
    python test_cross_lifecycle_e2e.py [--no-server]
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
SOURCE_FILE = Path(__file__).resolve().parents[3] / ".dev" / "table" / "我是测试知识文件.txt"
SECOND_FILE = Path(__file__).resolve().parents[3] / ".dev" / "table" / "doc" / "测试 DOCX 文档.docx"

FIRST_PROMPT = "你好，请记住暗号 CROSS-LIFETIME-2024，只回复这串暗号。"
SECOND_PROMPT = "上一轮对话中的暗号是什么？请告诉我。"


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


class ConversationResult:
    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.event_count = 0
        self.chat_content = ""
        self.text_content = ""
        self.tool_calls: List[str] = []
        self.errors: List[str] = []
        self.raw_lines: List[str] = []
        self.done = False
        self.response_text = ""
        self.execution_mode = None

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "event_count": self.event_count,
            "chat_length": len(self.chat_content),
            "text_length": len(self.text_content),
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "done": self.done,
            "response_text": self.response_text,
            "execution_mode": self.execution_mode,
        }


class APIClient:
    def __init__(self, base_url: str, user_id: int):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id

    def _json_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-User-ID": str(self.user_id),
        }

    def _auth_headers(self) -> dict:
        return {
            "X-User-ID": str(self.user_id),
        }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.request(method, url, headers=self._json_headers(), **kwargs)
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

    async def create_session(self, title: str) -> dict:
        return await self._request("POST", "/session/sessions", json={"title": title})

    async def get_session(self, session_id: int) -> dict:
        return await self._request("GET", f"/session/sessions/{session_id}")

    async def list_sessions(self) -> dict:
        return await self._request("GET", "/session/sessions")

    async def create_conversation(self, session_id: int, user_content: str) -> dict:
        return await self._request(
            "POST",
            f"/session/sessions/{session_id}/conversations",
            json={"user_content": user_content},
        )

    async def get_conversation(self, conversation_id: str) -> dict:
        return await self._request("GET", f"/session/conversations/{conversation_id}")

    async def get_workspace(self, workspace_id: str) -> dict:
        return await self._request("GET", f"/workspaces/{workspace_id}")

    async def list_workspace_files(self, workspace_id: str) -> dict:
        return await self._request("GET", f"/workspaces/{workspace_id}/files")

    async def upload_workspace_file(self, workspace_id: str, file_path: Path) -> dict:
        url = f"{self.base_url}/workspaces/{workspace_id}/files"
        mime_type = "text/plain; charset=utf-8"
        if file_path.suffix.lower() == ".docx":
            mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif file_path.suffix.lower() == ".pdf":
            mime_type = "application/pdf"
        elif file_path.suffix.lower() == ".xlsx":
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(file_path, "rb") as f:
                    response = await client.post(
                        url,
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

    async def stream_message(self, conversation_id: str, last_seq: int = 0):
        url = f"{self.base_url}/session/conversations/{conversation_id}/stream?last_seq={last_seq}"
        timeout = httpx.Timeout(connect=30.0, read=None, write=300.0, pool=300.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=self._json_headers()) as response:
                if response.status_code != 200:
                    try:
                        error = await response.aread()
                        yield {"type": "error", "raw": error.decode(), "status_code": response.status_code}
                    except Exception as e:
                        yield {"type": "error", "raw": str(e), "status_code": response.status_code}
                    return

                async for line in response.aiter_lines():
                    yield {"raw_line": line}


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
    timeout: float = 120.0,
) -> dict:
    deadline = time.time() + timeout
    last_result = None
    while time.time() < deadline:
        conversation_result = await api.get_conversation(conversation_id)
        last_result = conversation_result
        data = conversation_result.get("data") or {}
        if data.get("state") == expected_state:
            return conversation_result
        await asyncio.sleep(0.2)
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


async def collect_stream_output(api: APIClient, conversation_id: str, verbose: bool = True):
    result = ConversationResult(conversation_id)

    async for item in api.stream_message(conversation_id):
        raw_line = item.get("raw_line", "")
        if not raw_line.strip():
            continue

        result.raw_lines.append(raw_line)

        if raw_line.startswith(": heartbeat"):
            if verbose:
                print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
            continue

        if raw_line.startswith("data: "):
            result.event_count += 1
            json_str = raw_line[6:]

            try:
                data = json.loads(json_str)
                event_type = data.get("type", "unknown")

                if event_type == "chat_delta":
                    content = data.get("content", "")
                    result.chat_content += content
                    if verbose:
                        safe_print(f"{Colors.GREEN}[chat] {content}{Colors.ENDC}")
                elif event_type == "text_delta":
                    content = data.get("content", "")
                    result.text_content += content
                    if verbose:
                        safe_print(f"{Colors.CYAN}[text] {content}{Colors.ENDC}")
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
                        result.execution_mode = execution_mode
                        if verbose:
                            print(f"{Colors.YELLOW}[state] execution_mode: {execution_mode}{Colors.ENDC}")
                elif event_type == "done":
                    result.done = True
                    if verbose:
                        print(f"{Colors.GREEN}[done] Stream completed{Colors.ENDC}")
                elif event_type == "error":
                    error_content = data.get("content", "Unknown error")
                    result.errors.append(error_content)
                    if verbose:
                        print(f"{Colors.RED}[error] {error_content}{Colors.ENDC}")
                else:
                    if verbose:
                        print(f"{Colors.BLUE}[{event_type}] {json.dumps(data, ensure_ascii=False)[:120]}...{Colors.ENDC}")
            except json.JSONDecodeError as e:
                parse_error = f"JSON parse error: {e}"
                result.errors.append(parse_error)
                if verbose:
                    print(f"{Colors.RED}{parse_error}{Colors.ENDC}")

    return result


async def run_cross_lifecycle_test(api: APIClient, output_file: str) -> Dict:
    output_lines: List[str] = []
    errors: List[str] = []

    output_lines.append(f"# Cross Lifecycle E2E Test - {get_timestamp()}")
    output_lines.append("")

    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Cross Lifecycle E2E Test{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    if not SOURCE_FILE.exists():
        error_msg = f"Source file not found: {SOURCE_FILE}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    output_lines.append(f"- source_file: {SOURCE_FILE}")
    output_lines.append(f"- second_file: {SECOND_FILE}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Phase 1 - Before Restart]{Colors.ENDC}")
    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("Cross Lifecycle E2E Test")
    if session_result.get("code") != 200:
        error_msg = f"Session creation failed: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    session_id = session_result["data"]["id"]
    workspace_id = session_result["data"]["workspace_id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    print(f"{Colors.GREEN}    Workspace ID: {workspace_id}{Colors.ENDC}")
    output_lines.append(f"- session_id: {session_id}")
    output_lines.append(f"- workspace_id: {workspace_id}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 2] Uploading first file to workspace...{Colors.ENDC}")
    upload_result = await api.upload_workspace_file(workspace_id, SOURCE_FILE)
    if upload_result.get("code") != 200:
        error_msg = f"First file upload failed: {upload_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg], "session_id": session_id, "workspace_id": workspace_id}

    uploaded_files = upload_result.get("data") or []
    output_lines.append("## First Upload Result")
    output_lines.append(json.dumps(upload_result, ensure_ascii=False, indent=2))
    output_lines.append("")

    if len(uploaded_files) != 1:
        errors.append(f"Expected 1 uploaded file, got {len(uploaded_files)}")
    else:
        uploaded_file = uploaded_files[0]
        if uploaded_file.get("original_filename") != SOURCE_FILE.name:
            errors.append(f"Unexpected original filename: {uploaded_file.get('original_filename')}")

    print(f"{Colors.GREEN}    First file uploaded: {SOURCE_FILE.name}{Colors.ENDC}")

    print(f"{Colors.CYAN}[Step 3] Creating first conversation...{Colors.ENDC}")
    print(f"{Colors.DIM}    Prompt: {FIRST_PROMPT}{Colors.ENDC}")
    conversation_create_result = await api.create_conversation(session_id, FIRST_PROMPT)
    if conversation_create_result.get("code") != 200:
        error_msg = f"First conversation creation failed: {conversation_create_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {
            "success": False,
            "errors": errors + [error_msg],
            "session_id": session_id,
            "workspace_id": workspace_id,
        }

    first_conversation_id = conversation_create_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    First Conversation ID: {first_conversation_id}{Colors.ENDC}")
    output_lines.append(f"- first_conversation_id: {first_conversation_id}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 4] Receiving first stream...{Colors.ENDC}\n")
    first_result = await collect_stream_output(api, first_conversation_id, verbose=True)
    output_lines.append("## First Conversation Summary")
    output_lines.append(json.dumps(first_result.to_dict(), ensure_ascii=False, indent=2))
    output_lines.append("")

    if not first_result.done:
        errors.append("First conversation stream did not complete with done event")
    if first_result.errors:
        errors.extend(first_result.errors)

    print(f"\n{Colors.CYAN}[Step 5] Waiting for first conversation completion...{Colors.ENDC}")
    first_final_state = await wait_for_conversation_state(api, first_conversation_id, "completed")
    first_response = extract_response_text(first_final_state)
    print(f"{Colors.GREEN}    First response: {first_response}{Colors.ENDC}")

    first_state_data = first_final_state.get("data") or {}
    if first_state_data.get("state") != "completed":
        errors.append(f"First conversation final state is not completed: {first_state_data.get('state')}")

    if "CROSS-LIFETIME-2024" not in first_response:
        errors.append(f"First conversation did not return the code word: {first_response}")

    print(f"\n{Colors.CYAN}[Step 6] Listing workspace files before restart...{Colors.ENDC}")
    files_before_restart = await api.list_workspace_files(workspace_id)
    output_lines.append("## Files Before Restart")
    output_lines.append(json.dumps(files_before_restart, ensure_ascii=False, indent=2))
    output_lines.append("")

    files_before_data = files_before_restart.get("data") or []
    file_names_before = [f.get("name") for f in files_before_data]
    print(f"{Colors.GREEN}    Files: {file_names_before}{Colors.ENDC}")

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "session_id": session_id,
        "workspace_id": workspace_id,
        "first_conversation_id": first_conversation_id,
        "first_response": first_response,
        "files_before_restart": file_names_before,
        "output_lines": output_lines,
    }


async def run_after_restart_test(
    api: APIClient,
    session_id: int,
    workspace_id: str,
    first_conversation_id: str,
    output_lines: List[str],
    errors: List[str],
) -> Dict:
    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Phase 2 - After Restart{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    print(f"{Colors.CYAN}[Step 7] Verifying session accessibility after restart...{Colors.ENDC}")
    session_result = await api.get_session(session_id)
    if session_result.get("code") != 200:
        error_msg = f"Session not accessible after restart: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        errors.append(error_msg)
        return {"success": False, "errors": errors, "output_lines": output_lines}

    print(f"{Colors.GREEN}    Session {session_id} is accessible{Colors.ENDC}")
    output_lines.append(f"- session_accessible: true")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 8] Listing workspace files after restart...{Colors.ENDC}")
    files_after_restart = await api.list_workspace_files(workspace_id)
    output_lines.append("## Files After Restart")
    output_lines.append(json.dumps(files_after_restart, ensure_ascii=False, indent=2))
    output_lines.append("")

    files_after_data = files_after_restart.get("data") or []
    file_names_after = [f.get("name") for f in files_after_data]
    print(f"{Colors.GREEN}    Files: {file_names_after}{Colors.ENDC}")

    if SOURCE_FILE.name not in file_names_after:
        errors.append(f"First uploaded file not found after restart: {SOURCE_FILE.name}")

    print(f"{Colors.CYAN}[Step 9] Creating second conversation to verify history...{Colors.ENDC}")
    print(f"{Colors.DIM}    Prompt: {SECOND_PROMPT}{Colors.ENDC}")
    second_conv_result = await api.create_conversation(session_id, SECOND_PROMPT)
    if second_conv_result.get("code") != 200:
        error_msg = f"Second conversation creation failed: {second_conv_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        errors.append(error_msg)
        return {"success": False, "errors": errors, "output_lines": output_lines}

    second_conversation_id = second_conv_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    Second Conversation ID: {second_conversation_id}{Colors.ENDC}")
    output_lines.append(f"- second_conversation_id: {second_conversation_id}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 10] Receiving second stream...{Colors.ENDC}\n")
    second_result = await collect_stream_output(api, second_conversation_id, verbose=True)
    output_lines.append("## Second Conversation Summary")
    output_lines.append(json.dumps(second_result.to_dict(), ensure_ascii=False, indent=2))
    output_lines.append("")

    if not second_result.done:
        errors.append("Second conversation stream did not complete with done event")
    if second_result.errors:
        errors.extend(second_result.errors)

    print(f"\n{Colors.CYAN}[Step 11] Waiting for second conversation completion...{Colors.ENDC}")
    second_final_state = await wait_for_conversation_state(api, second_conversation_id, "completed")
    second_response = extract_response_text(second_final_state)
    print(f"{Colors.GREEN}    Second response: {second_response}{Colors.ENDC}")

    second_state_data = second_final_state.get("data") or {}
    if second_state_data.get("state") != "completed":
        errors.append(f"Second conversation final state is not completed: {second_state_data.get('state')}")

    if "CROSS-LIFETIME-2024" not in second_response:
        errors.append(f"Second conversation did not inherit history - code word not found: {second_response}")

    print(f"\n{Colors.CYAN}[Step 12] Uploading second file after restart...{Colors.ENDC}")
    if SECOND_FILE.exists():
        second_upload_result = await api.upload_workspace_file(workspace_id, SECOND_FILE)
        output_lines.append("## Second Upload Result")
        output_lines.append(json.dumps(second_upload_result, ensure_ascii=False, indent=2))
        output_lines.append("")

        if second_upload_result.get("code") != 200:
            error_msg = f"Second file upload failed: {second_upload_result.get('message', 'Unknown error')}"
            print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
            errors.append(error_msg)
        else:
            print(f"{Colors.GREEN}    Second file uploaded: {SECOND_FILE.name}{Colors.ENDC}")

            print(f"{Colors.CYAN}[Step 13] Verifying both files in workspace...{Colors.ENDC}")
            final_files = await api.list_workspace_files(workspace_id)
            final_files_data = final_files.get("data") or []
            final_file_names = [f.get("name") for f in final_files_data]
            print(f"{Colors.GREEN}    Final files: {final_file_names}{Colors.ENDC}")

            output_lines.append("## Final Files")
            output_lines.append(json.dumps(final_files, ensure_ascii=False, indent=2))
            output_lines.append("")

            if SOURCE_FILE.name not in final_file_names:
                errors.append(f"First file missing after second upload: {SOURCE_FILE.name}")
            if SECOND_FILE.name not in final_file_names:
                errors.append(f"Second file not found in workspace: {SECOND_FILE.name}")
    else:
        print(f"{Colors.YELLOW}    Second file not found, skipping: {SECOND_FILE}{Colors.ENDC}")

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "second_conversation_id": second_conversation_id,
        "second_response": second_response,
        "output_lines": output_lines,
    }


async def main(no_server: bool = False):
    backend_process = None
    all_errors: List[str] = []
    output_file = Path(__file__).parent / "output" / f"cross_lifecycle_e2e_{get_timestamp()}.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        if not no_server:
            backend_process = start_backend()
            if not wait_for_backend():
                all_errors.append("Backend failed to start")
                return
        else:
            if not wait_for_backend():
                all_errors.append("Backend not available")
                return

        api = APIClient(BASE_URL, user_id=1)

        phase1_result = await run_cross_lifecycle_test(api, str(output_file))
        all_errors.extend(phase1_result.get("errors", []))

        if not phase1_result.get("success"):
            print(f"\n{Colors.RED}Phase 1 failed, aborting test{Colors.ENDC}")
            return

        session_id = phase1_result["session_id"]
        workspace_id = phase1_result["workspace_id"]
        first_conversation_id = phase1_result["first_conversation_id"]
        output_lines = phase1_result.get("output_lines", [])

        if not no_server and backend_process:
            print(f"\n{Colors.YELLOW}{'=' * 72}{Colors.ENDC}")
            print(f"{Colors.YELLOW}  Restarting Backend...{Colors.ENDC}")
            print(f"{Colors.YELLOW}{'=' * 72}{Colors.ENDC}\n")

            stop_backend(backend_process)
            await asyncio.sleep(2)

            backend_process = start_backend()
            if not wait_for_backend():
                all_errors.append("Backend failed to restart")
                return

            print(f"{Colors.GREEN}Backend restarted successfully{Colors.ENDC}")

        phase2_result = await run_after_restart_test(
            api,
            session_id,
            workspace_id,
            first_conversation_id,
            output_lines,
            all_errors,
        )
        all_errors.extend(phase2_result.get("errors", []))
        output_lines = phase2_result.get("output_lines", output_lines)

    finally:
        if backend_process:
            stop_backend(backend_process)

    output_lines.append("## Test Summary")
    output_lines.append(f"- total_errors: {len(all_errors)}")
    output_lines.append(f"- success: {len(all_errors) == 0}")
    if all_errors:
        output_lines.append("- errors:")
        for error in all_errors:
            output_lines.append(f"  - {error}")
    output_lines.append("")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test Summary{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"  Output file: {output_file}")
    print(f"  Total errors: {len(all_errors)}")
    if all_errors:
        print(f"{Colors.RED}  Errors:{Colors.ENDC}")
        for error in all_errors:
            print(f"{Colors.RED}    - {error}{Colors.ENDC}")
    else:
        print(f"{Colors.GREEN}  All tests passed!{Colors.ENDC}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross Lifecycle E2E Test")
    parser.add_argument("--no-server", action="store_true", help="Do not start/stop backend server")
    args = parser.parse_args()

    asyncio.run(main(no_server=args.no_server))
