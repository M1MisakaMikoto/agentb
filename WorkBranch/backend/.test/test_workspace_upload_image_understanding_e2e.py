#!/usr/bin/env python3
"""
Workspace Upload Image Understanding E2E Test

测试目标:
1. 模拟用户将测试图片上传到 session 对应工作区
2. 创建对话，使用 user_content_parts 发送“文本 + 图片文件引用(file_ref)”的多模态消息
3. 验证流式执行完成、执行模式为 DIRECT，并且没有因为图片输入报错
4. 验证最终回复能够识别图表的核心信息（算法名称、相对趋势）

Usage:
    python test_workspace_upload_image_understanding_e2e.py [--no-server]
"""

import argparse
import asyncio
import json
import mimetypes
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
SOURCE_FILE = Path(__file__).resolve().parents[3] / ".dev" / "table" / "测试图片.png"
PROMPT_TEXT = (
    "请分析这张图片。"
    "这是一个多步骤但只读的任务："
    "先识别图片是不是图表；"
    "再说明图里出现了哪些算法或曲线名称；"
    "再总结整体趋势，并判断哪条曲线最慢、哪条最快。"
    "请基于图片本身作答，不要只根据文件名猜测。"
    "不要修改任何文件，不要创建新文件，最后用简短中文结论回答。"
)


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

    async def create_conversation(self, session_id: int, user_content_parts: List[dict]) -> dict:
        return await self._request(
            "POST",
            f"/session/sessions/{session_id}/conversations",
            json={"user_content_parts": user_content_parts},
        )

    async def get_conversation(self, conversation_id: str) -> dict:
        return await self._request("GET", f"/session/conversations/{conversation_id}")

    async def get_workspace(self, workspace_id: str) -> dict:
        return await self._request("GET", f"/workspaces/{workspace_id}")

    async def list_workspace_files(self, workspace_id: str) -> dict:
        return await self._request("GET", f"/workspaces/{workspace_id}/files")

    async def upload_workspace_file(self, workspace_id: str, file_path: Path) -> dict:
        url = f"{self.base_url}/workspaces/{workspace_id}/files"
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(file_path, "rb") as f:
                    response = await client.post(
                        url,
                        headers=self._auth_headers(),
                        files=[("files", (file_path.name, f, mime))],
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

    async def stream_message(self, conversation_id: str):
        url = f"{self.base_url}/session/conversations/{conversation_id}/messages/stream"
        timeout = httpx.Timeout(connect=30.0, read=None, write=300.0, pool=300.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=self._json_headers()) as response:
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


async def wait_for_conversation_state(api: APIClient, conversation_id: str, expected_state: str, timeout: float = 120.0) -> dict:
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


async def run_workspace_upload_image_understanding_test(api: APIClient, output_file: str) -> Dict:
    output_lines: List[str] = []
    errors: List[str] = []

    output_lines.append(f"# Workspace Upload Image Understanding E2E - {get_timestamp()}")
    output_lines.append("")

    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Workspace Upload Image Understanding E2E Test{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    if not SOURCE_FILE.exists():
        error_msg = f"Source file not found: {SOURCE_FILE}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    output_lines.append(f"- source_file: {SOURCE_FILE}")
    output_lines.append(f"- prompt_text: {PROMPT_TEXT}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("Workspace Upload Image Understanding E2E")
    if session_result.get("code") != 200:
        error_msg = f"Session creation failed: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    session_id = session_result["data"]["id"]
    workspace_id = session_result["data"]["workspace_id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    print(f"{Colors.GREEN}    Workspace ID: {workspace_id}{Colors.ENDC}")

    print(f"{Colors.CYAN}[Step 2] Uploading image to workspace...{Colors.ENDC}")
    upload_result = await api.upload_workspace_file(workspace_id, SOURCE_FILE)
    if upload_result.get("code") != 200:
        error_msg = f"File upload failed: {upload_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg], "session_id": session_id, "workspace_id": workspace_id}

    uploaded_files = upload_result.get("data") or []
    if len(uploaded_files) != 1:
        errors.append(f"Expected 1 uploaded file, got {len(uploaded_files)}")
        saved_name = SOURCE_FILE.name
    else:
        saved_name = uploaded_files[0].get("saved_as") or SOURCE_FILE.name

    output_lines.append(f"- file_ref: {saved_name}")
    output_lines.append("")

    user_content_parts = [
        {"type": "text", "text": PROMPT_TEXT},
        {"type": "image", "file_ref": saved_name, "name": SOURCE_FILE.name},
    ]

    print(f"{Colors.CYAN}[Step 3] Creating multimodal conversation...{Colors.ENDC}")
    conversation_create_result = await api.create_conversation(session_id, user_content_parts)
    if conversation_create_result.get("code") != 200:
        error_msg = f"Conversation creation failed: {conversation_create_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {
            "success": False,
            "errors": errors + [error_msg],
            "session_id": session_id,
            "workspace_id": workspace_id,
        }

    conversation_id = conversation_create_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")

    result = ConversationResult(conversation_id)
    output_lines.append("## Raw Stream Data")
    output_lines.append("```json")

    print(f"{Colors.CYAN}[Step 4] Receiving stream...{Colors.ENDC}\n")
    stream_deadline = time.time() + 180.0
    async for item in api.stream_message(conversation_id):
        if time.time() > stream_deadline:
            result.errors.append("Stream timed out after 180 seconds")
            print(f"{Colors.RED}[error] Stream timed out after 180 seconds{Colors.ENDC}")
            break

        raw_line = item.get("raw_line", "")
        if not raw_line.strip():
            continue

        result.raw_lines.append(raw_line)

        if raw_line.startswith(": heartbeat"):
            print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
            continue

        if raw_line.startswith("data: "):
            result.event_count += 1
            json_str = raw_line[6:]
            output_lines.append(json_str)

            try:
                data = json.loads(json_str)
                event_type = data.get("type", "unknown")

                if event_type == "chat_delta":
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
                        result.execution_mode = execution_mode
                        print(f"{Colors.YELLOW}[state] execution_mode: {execution_mode}{Colors.ENDC}")
                elif event_type == "done":
                    result.done = True
                    print(f"{Colors.GREEN}[done] Stream completed{Colors.ENDC}")
                elif event_type == "error":
                    error_content = data.get("content", "Unknown error")
                    result.errors.append(error_content)
                    print(f"{Colors.RED}[error] {error_content}{Colors.ENDC}")
                else:
                    print(f"{Colors.BLUE}[{event_type}] {json.dumps(data, ensure_ascii=False)[:120]}...{Colors.ENDC}")
            except json.JSONDecodeError as e:
                parse_error = f"JSON parse error: {e}"
                result.errors.append(parse_error)
                print(f"{Colors.RED}{parse_error}{Colors.ENDC}")

    output_lines.append("```")
    output_lines.append("")

    print(f"\n{Colors.CYAN}[Step 5] Waiting for completed state...{Colors.ENDC}")
    final_state = await wait_for_conversation_state(api, conversation_id, "completed")
    result.response_text = extract_response_text(final_state)
    print(f"{Colors.GREEN}    Final response: {result.response_text}{Colors.ENDC}")

    output_lines.append("## Conversation Summary")
    output_lines.append(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    output_lines.append("")

    if not result.done:
        errors.append("Stream did not complete with done event")
    if result.errors:
        errors.extend(result.errors)

    final_state_data = final_state.get("data") or {}
    if final_state_data.get("state") != "completed":
        errors.append(f"Conversation final state is not completed: {final_state_data.get('state')}")

    if result.execution_mode != "DIRECT":
        errors.append(f"Expected DIRECT execution mode, got: {result.execution_mode}")

    forbidden_tools = {"write_file", "read_file", "read_document"}
    used_forbidden_tools = [tool for tool in result.tool_calls if tool in forbidden_tools]
    if used_forbidden_tools:
        errors.append(f"Image understanding should go through native multimodal chat, but used tools: {used_forbidden_tools}")

    response = result.response_text
    lowered = response.lower()
    keyword_groups = {
        "insertion": ["Insertion Sort", "插入排序", "insertion"],
        "merge": ["Merge Sort", "归并排序", "merge"],
        "quick": ["Quick Sort", "快速排序", "quick"],
    }
    for label, candidates in keyword_groups.items():
        if not any(candidate in response or candidate.lower() in lowered for candidate in candidates):
            errors.append(f"Final response missing expected algorithm keyword group '{label}': {response!r}")

    trend_candidates = ["最快", "最慢", "增长", "趋势", "上升", "最高", "最低"]
    if not any(token in response for token in trend_candidates):
        errors.append(f"Final response missing trend summary keywords: {response!r}")

    workspace_files_result = await api.list_workspace_files(workspace_id)
    if workspace_files_result.get("code") != 200:
        errors.append(f"List workspace files failed: {workspace_files_result.get('message', 'Unknown error')}")
    else:
        workspace_files = workspace_files_result.get("data") or []
        workspace_paths = {item.get("path") for item in workspace_files if item.get("path")}
        if SOURCE_FILE.name not in workspace_paths and saved_name not in workspace_paths:
            errors.append(f"Uploaded image is missing from workspace listing: {workspace_paths}")

    output_lines.append("## Final Errors")
    output_lines.append(json.dumps(errors, ensure_ascii=False, indent=2))
    output_lines.append("")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\n{Colors.CYAN}Output saved to: {output_file}{Colors.ENDC}")

    if errors:
        print(f"\n{Colors.RED}{Colors.BOLD}测试失败!{Colors.ENDC}")
        for err in errors:
            print(f"{Colors.RED}  - {err}{Colors.ENDC}")
    else:
        print(f"\n{Colors.GREEN}{Colors.BOLD}测试通过!{Colors.ENDC}")

    return {
        "success": not errors,
        "errors": errors,
        "session_id": session_id,
        "workspace_id": workspace_id,
        "conversation": result.to_dict(),
        "output_file": output_file,
    }


async def main_async(no_server: bool) -> int:
    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = str(output_dir / f"workspace_upload_image_understanding_e2e_{get_timestamp()}.md")

    process = None
    try:
        if not no_server:
            process = start_backend()
            if not wait_for_backend():
                return 1

        api = APIClient(BASE_URL, user_id=1)
        result = await run_workspace_upload_image_understanding_test(api, output_file)
        return 0 if result.get("success") else 1
    finally:
        if process is not None:
            stop_backend(process)


def main():
    parser = argparse.ArgumentParser(description="Workspace Upload Image Understanding E2E Test")
    parser.add_argument("--no-server", action="store_true", help="Do not start backend automatically")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args.no_server)))


if __name__ == "__main__":
    main()
