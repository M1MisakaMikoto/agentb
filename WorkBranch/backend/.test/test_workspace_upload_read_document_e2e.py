#!/usr/bin/env python3
"""
Workspace Upload Read Document E2E Test

测试目标:
1. 模拟用户将三个文档文件上传到 session 对应工作区
2. 创建对话，让 agent 在 DIRECT 模式下围绕这三个文档完成一个分阶段只读任务
3. 验证流式执行完成、执行模式为 DIRECT、并尽量触发 read_document 工具
4. 输出 agent 对三个文档内容的总结，且没有发生写文件行为

Usage:
    python test_workspace_upload_read_document_e2e.py [--no-server]
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


MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
SOURCE_FILES = [
    Path(__file__).resolve().parents[3] / ".dev" / "table" / "城市桥梁养护技术规程（标准文本）.pdf",
]
PROMPT = (
    "请在当前工作区内完成一个只读任务。工作区里已经有一个文件，文件名是\"城市桥梁养护技术规程（标准文本）.pdf\"。"
    "请先查看工作区里有哪些文件并确认该文件位置；"
    "再使用 read_document 工具读取这个文件；"
    "然后总结文件的主要内容。"
    "不要修改任何文件，不要创建新文件，最后输出简短结论。"
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
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(file_path, "rb") as f:
                    response = await client.post(
                        url,
                        headers=self._auth_headers(),
                        files=[("files", (file_path.name, f, MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")))],
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
            async with client.stream("GET", url, headers=self._auth_headers()) as response:
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


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


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


async def run_workspace_upload_read_document_test(api: APIClient, output_file: str) -> Dict:
    output_lines: List[str] = []
    errors: List[str] = []

    output_lines.append(f"# Workspace Upload Read Document E2E - {get_timestamp()}")
    output_lines.append("")

    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Workspace Upload Read Document E2E Test{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    missing_files = [str(path) for path in SOURCE_FILES if not path.exists()]
    if missing_files:
        error_msg = f"Source files not found: {missing_files}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}

    output_lines.append("## Source Files")
    output_lines.append(json.dumps([str(path) for path in SOURCE_FILES], ensure_ascii=False, indent=2))
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("Workspace Upload Read Document E2E")
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

    print(f"{Colors.CYAN}[Step 2] Uploading source files to workspace...{Colors.ENDC}")
    upload_results = []
    for source_file in SOURCE_FILES:
        upload_result = await api.upload_workspace_file(workspace_id, source_file)
        upload_results.append({"file": source_file.name, "result": upload_result})
        if upload_result.get("code") != 200:
            error_msg = f"File upload failed for {source_file.name}: {upload_result.get('message', 'Unknown error')}"
            print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
            return {"success": False, "errors": [error_msg], "session_id": session_id, "workspace_id": workspace_id}
        print(f"{Colors.GREEN}    Uploaded: {source_file.name}{Colors.ENDC}")

    output_lines.append("## Upload Result")
    output_lines.append(json.dumps(upload_results, ensure_ascii=False, indent=2))
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 3] Resolving workspace directory...{Colors.ENDC}")
    workspace_result = await api.get_workspace(workspace_id)
    if workspace_result.get("code") != 200:
        error_msg = f"Get workspace failed: {workspace_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {
            "success": False,
            "errors": errors + [error_msg],
            "session_id": session_id,
            "workspace_id": workspace_id,
        }

    workspace_dir_raw = (workspace_result.get("data") or {}).get("dir")
    workspace_dir = Path(workspace_dir_raw) if workspace_dir_raw else None
    output_lines.append("## Workspace Result")
    output_lines.append(json.dumps(workspace_result, ensure_ascii=False, indent=2))
    output_lines.append("")

    if workspace_dir is None or not workspace_dir.exists():
        errors.append(f"Workspace directory does not exist: {workspace_dir_raw}")
        workspace_dir = None
    else:
        print(f"{Colors.GREEN}    Workspace Dir: {workspace_dir}{Colors.ENDC}")

    print(f"{Colors.CYAN}[Step 4] Creating conversation...{Colors.ENDC}")
    print(f"{Colors.DIM}    Prompt: {PROMPT}{Colors.ENDC}")
    conversation_create_result = await api.create_conversation(session_id, PROMPT)
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
    output_lines.append(f"- conversation_id: {conversation_id}")
    output_lines.append("")

    result = ConversationResult(conversation_id)
    output_lines.append("## Raw Stream Data")
    output_lines.append("```json")

    print(f"{Colors.CYAN}[Step 5] Receiving stream...{Colors.ENDC}\n")
    async for item in api.stream_message(conversation_id):
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

    print(f"\n{Colors.CYAN}[Step 6] Waiting for completed state...{Colors.ENDC}")
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

    if "write_file" in result.tool_calls:
        errors.append(f"write_file should not be used in this read-only test: {result.tool_calls}")

    if "read_document" not in result.tool_calls:
        errors.append(f"read_document was not observed in tool calls: {result.tool_calls}")

    expected_workspace_names = {path.name for path in SOURCE_FILES}

    print(f"{Colors.CYAN}[Step 7] Listing workspace files...{Colors.ENDC}")
    workspace_files_result = await api.list_workspace_files(workspace_id)
    output_lines.append("## Workspace Files")
    output_lines.append(json.dumps(workspace_files_result, ensure_ascii=False, indent=2))
    output_lines.append("")

    if workspace_files_result.get("code") != 200:
        errors.append(f"List workspace files failed: {workspace_files_result.get('message', 'Unknown error')}")
        workspace_files = []
    else:
        workspace_files = workspace_files_result.get("data") or []

    workspace_paths = {item.get("path") for item in workspace_files if item.get("path")}
    missing_in_listing = sorted(name for name in expected_workspace_names if name not in workspace_paths)
    if missing_in_listing:
        errors.append(f"Uploaded files are missing from workspace listing: {missing_in_listing}; got={sorted(workspace_paths)}")

    if workspace_dir is not None:
        for source_file in SOURCE_FILES:
            uploaded_workspace_file = workspace_dir / source_file.name
            if not uploaded_workspace_file.exists():
                errors.append(f"Uploaded file missing on disk: {uploaded_workspace_file}")

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
    }


async def main():
    parser = argparse.ArgumentParser(description="Workspace Upload Read Document E2E Test")
    parser.add_argument("--no-server", action="store_true", help="Do not start server automatically")
    parser.add_argument("--user-id", type=int, default=1, help="User ID for API requests")
    args = parser.parse_args()

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

        api = APIClient(BASE_URL, user_id=args.user_id)
        logs_dir = Path(__file__).parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        output_file = logs_dir / f"workspace_upload_read_document_e2e_{get_timestamp()}.md"

        result = await run_workspace_upload_read_document_test(api, str(output_file))
        return 0 if result.get("success") else 1

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
