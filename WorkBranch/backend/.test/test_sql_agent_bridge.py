#!/usr/bin/env python3
"""
SQL Tool Agent Test - 智慧管养系统桥梁数据统计

测试目标:
1. 使用现有 sql_tools_config.json 配置
2. 发送提示词让 Agent 调用 SQL 工具
3. 不修改任何配置文件

Usage:
    python test_sql_agent_bridge.py [--no-server]
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
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import httpx


BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "setting.json"

TEST_PROMPT = "我需要统计智慧管养系统中桥梁基础数据的总数，需要调用工具查数据库"


class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    MAGENTA = "\033[35m"
    BLUE = "\033[94m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


class ConversationResult:
    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.event_count = 0
        self.chat_content = ""
        self.tool_calls = []
        self.tool_args_list = []
        self.tool_results = []
        self.errors = []
        self.done = False
        self.response_text = ""
        self.execution_mode = None
        self.raw_lines = []

    def to_dict(self):
        return {
            "conversation_id": self.conversation_id,
            "event_count": self.event_count,
            "chat_length": len(self.chat_content),
            "tool_calls": self.tool_calls,
            "tool_args_list": self.tool_args_list,
            "tool_results": self.tool_results,
            "errors": self.errors,
            "done": self.done,
            "response_text": self.response_text,
            "execution_mode": self.execution_mode,
        }


def load_json_file(file_path: Path) -> dict:
    return json.loads(file_path.read_text(encoding="utf-8"))


def ensure_sql_query_enabled(settings: dict) -> list:
    missing = []
    tool_permissions = settings.get("tool_permissions") or {}
    for agent_type in ["director_agent", "plan_agent", "review_agent", "explore_agent", "admin_agent"]:
        allowed = ((tool_permissions.get(agent_type) or {}).get("allowed") or [])
        if "sql_query" not in allowed:
            missing.append(agent_type)
    return missing


def wait_for_backend(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f"{BASE_URL}/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (URLError, Exception):
            pass
        time.sleep(0.5)
    return False


def start_backend():
    command = [
        sys.executable,
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
        "cwd": str(BACKEND_DIR),
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


def stop_backend(process):
    if process.poll() is not None:
        return

    print(f"{Colors.CYAN}Stopping backend...{Colors.ENDC}")

    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGTERM)

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


class APIClient:
    def __init__(self, base_url: str, user_id: int = 1):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id

    def _json_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
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

    async def create_session(self, title: str = "SQL Agent Test") -> dict:
        return await self._request("POST", "/session/sessions", json={"title": title})

    async def create_conversation(self, session_id: int, user_content: str) -> dict:
        return await self._request(
            "POST",
            f"/session/sessions/{session_id}/conversations",
            json={"user_content": user_content},
        )

    async def get_conversation(self, conversation_id: str) -> dict:
        return await self._request("GET", f"/session/conversations/{conversation_id}")

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
        return assistant_content

    text_parts = []
    for event in events:
        if event.get("type") == "chat":
            text_parts.append(event.get("content", ""))
    return "".join(text_parts)


async def run_test(api: APIClient, output_file: str):
    output_lines = []
    errors = []

    output_lines.append("# SQL Tool Agent Test - 桥梁数据统计")
    output_lines.append(f"\n提示词: {TEST_PROMPT}")
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("SQL Agent Bridge Test")
    if session_result.get("code") != 200:
        error_msg = f"Session creation failed: {session_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}
    
    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    if not session_id:
        errors.append(f"Failed to get session_id: {session_result}")
        return {"success": False, "errors": errors}
    
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    print(f"{Colors.GREEN}    Workspace ID: {workspace_id}{Colors.ENDC}")

    output_lines.append(f"- session_id: {session_id}")
    output_lines.append(f"- workspace_id: {workspace_id}")

    print(f"{Colors.CYAN}[Step 2] Creating conversation...{Colors.ENDC}")
    print(f"{Colors.YELLOW}    Prompt: {TEST_PROMPT}{Colors.ENDC}")
    conversation_create_result = await api.create_conversation(session_id, TEST_PROMPT)
    if conversation_create_result.get("code") != 200:
        error_msg = f"Conversation creation failed: {conversation_create_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {"success": False, "errors": [error_msg]}
    
    conversation_id = conversation_create_result.get("data", {}).get("conversation_id")
    if not conversation_id:
        errors.append(f"Failed to get conversation_id: {conversation_create_result}")
        return {"success": False, "errors": errors}
    
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")

    output_lines.append(f"- conversation_id: {conversation_id}")

    result = ConversationResult(conversation_id)
    output_lines.append("\n## Raw Stream Data")
    output_lines.append("```json")

    print(f"{Colors.CYAN}[Step 3] Receiving stream...{Colors.ENDC}\n")
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
                    print(f"{Colors.GREEN}[chat] {content}{Colors.ENDC}")
                elif event_type == "tool_call":
                    metadata = data.get("metadata", {})
                    tool_name = metadata.get("tool_name", "unknown")
                    tool_args = metadata.get("tool_args", {})
                    result.tool_calls.append(tool_name)
                    result.tool_args_list.append({"tool_name": tool_name, "args": tool_args})
                    print(f"{Colors.MAGENTA}[tool_call] {tool_name}{Colors.ENDC}")
                    print(f"{Colors.MAGENTA}    Args: {json.dumps(tool_args, ensure_ascii=False)}{Colors.ENDC}")
                elif event_type == "tool_res":
                    metadata = data.get("metadata", {})
                    result.tool_results.append(metadata)
                    success = metadata.get("success", False)
                    color = Colors.GREEN if success else Colors.RED
                    print(f"{Colors.BLUE}[tool_res] success={success}{Colors.ENDC}")
                    if metadata.get("result"):
                        print(f"{Colors.BLUE}    Result: {str(metadata.get('result', ''))[:300]}...{Colors.ENDC}")
                    if metadata.get("error"):
                        print(f"{Colors.RED}    Error: {metadata.get('error')}{Colors.ENDC}")
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
            except json.JSONDecodeError as e:
                parse_error = f"JSON parse error: {e}"
                result.errors.append(parse_error)
                print(f"{Colors.RED}{parse_error}{Colors.ENDC}")

    output_lines.append("```")
    output_lines.append("")

    print(f"\n{Colors.CYAN}[Step 4] Waiting for completed state...{Colors.ENDC}")
    final_state = await wait_for_conversation_state(api, conversation_id, "completed")
    result.response_text = extract_response_text(final_state)
    print(f"{Colors.GREEN}    Final response:{Colors.ENDC}")
    print(f"{Colors.GREEN}    {result.response_text[:500]}{'...' if len(result.response_text) > 500 else ''}{Colors.ENDC}")

    output_lines.append("\n## Conversation Summary")
    output_lines.append(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    output_lines.append("")

    sql_query_calls = [tool for tool in result.tool_calls if tool == "sql_query"]
    if not sql_query_calls:
        errors.append(f"sql_query was not called: {result.tool_calls}")

    output_lines.append("\n## Test Result")
    output_lines.append(json.dumps({"errors": errors, "success": not errors}, ensure_ascii=False, indent=2))

    Path(output_file).write_text("\n".join(output_lines), encoding="utf-8")
    print(f"\n{Colors.CYAN}Output saved to: {output_file}{Colors.ENDC}")

    if errors:
        print(f"\n{Colors.RED}{Colors.BOLD}测试失败!{Colors.ENDC}")
        for err in errors:
            print(f"{Colors.RED}  - {err}{Colors.ENDC}")
    else:
        print(f"\n{Colors.GREEN}{Colors.BOLD}测试通过!{Colors.ENDC}")

    return {"success": not errors, "errors": errors, "conversation": result.to_dict()}


async def main():
    parser = argparse.ArgumentParser(description="SQL Tool Agent Test - 桥梁数据统计")
    parser.add_argument("--no-server", action="store_true", help="Do not start server automatically")
    parser.add_argument("--user-id", type=int, default=1, help="User ID for API requests")
    args = parser.parse_args()

    backend_process = None
    started_backend = False

    try:
        settings = load_json_file(SETTINGS_PATH)
        missing_agents = ensure_sql_query_enabled(settings)
        if missing_agents:
            print(f"{Colors.RED}setting.json 未放通 sql_query: {missing_agents}{Colors.ENDC}")
            return 1
        print(f"{Colors.GREEN}setting.json 已放通 sql_query{Colors.ENDC}")

        if args.no_server:
            if not wait_for_backend(timeout=2):
                print(f"{Colors.RED}Backend not running, please start or remove --no-server{Colors.ENDC}")
                return 1
        else:
            if not wait_for_backend(timeout=2):
                print(f"{Colors.CYAN}Initializing database...{Colors.ENDC}")
                if str(BACKEND_DIR) not in sys.path:
                    sys.path.insert(0, str(BACKEND_DIR))
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
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = logs_dir / f"sql_agent_bridge_{timestamp}.md"

        result = await run_test(api, str(output_file))
        return 0 if result.get("success") else 1

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted{Colors.ENDC}")
        return 130
    finally:
        if started_backend and backend_process is not None:
            stop_backend(backend_process)

        try:
            if str(BACKEND_DIR) not in sys.path:
                sys.path.insert(0, str(BACKEND_DIR))
            from singleton import clear_all_singletons_async
            await clear_all_singletons_async()
        except Exception:
            pass


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
