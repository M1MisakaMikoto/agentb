#!/usr/bin/env python3
"""
Simple E2E Test - Single Conversation with Raw Stream Output

Usage:
    python simple_e2e_stream.py [--no-server] -q "你的问题"
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
from typing import Optional
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


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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

    async def stream_message(self, conversation_id: str):
        url = f"{self.base_url}/session/conversations/{conversation_id}/messages/stream"
        headers = self._headers()

        async with httpx.AsyncClient(timeout=300.0) as client:
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


async def run_test(question: str, user_id: int, output_file: str):
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  E2E Stream Test - {get_timestamp()}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    api = APIClient(BASE_URL, user_id)

    raw_output_lines = []

    def log_raw(line: str):
        raw_output_lines.append(line)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(raw_output_lines))

    log_raw(f"# E2E Stream Test - {get_timestamp()}")
    log_raw(f"# Question: {question}")
    log_raw("")

    print(f"{Colors.CYAN}[1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("Stream Test")
    if session_result.get("code") != 200:
        print(f"{Colors.RED}Failed: {session_result.get('message')}{Colors.ENDC}")
        return False

    session_id = session_result["data"]["id"]
    print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
    log_raw(f"## Session ID: {session_id}")

    print(f"{Colors.CYAN}[2] Creating conversation...{Colors.ENDC}")
    print(f"{Colors.DIM}    Question: {question}{Colors.ENDC}")
    conv_result = await api.create_conversation(session_id, question)
    if conv_result.get("code") != 200:
        print(f"{Colors.RED}Failed: {conv_result.get('message')}{Colors.ENDC}")
        return False

    conversation_id = conv_result["data"]["conversation_id"]
    print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")
    log_raw(f"## Conversation ID: {conversation_id}")
    log_raw("")

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Raw Stream Data{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    log_raw("## Raw Stream Data")
    log_raw("```")

    print(f"{Colors.CYAN}[3] Receiving stream...{Colors.ENDC}\n")

    event_count = 0
    text_content = ""
    thinking_content = ""

    async for item in api.stream_message(conversation_id):
        raw_line = item.get("raw_line", "")
        
        if not raw_line.strip():
            continue

        log_raw(raw_line)

        if raw_line.startswith(": heartbeat"):
            print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
            continue

        if raw_line.startswith("data: "):
            event_count += 1
            json_str = raw_line[6:]
            
            try:
                data = json.loads(json_str)
                event_type = data.get("type", "unknown")
                
                print(f"\n{Colors.BLUE}--- Event #{event_count} ---{Colors.ENDC}")
                print(f"{Colors.YELLOW}Type: {event_type}{Colors.ENDC}")
                
                if event_type == "thinking_delta":
                    content = data.get("content", "")
                    thinking_content += content
                    print(f"{Colors.DIM}Content: {content}{Colors.ENDC}")
                elif event_type == "text_delta":
                    content = data.get("content", "")
                    text_content += content
                    print(f"{Colors.GREEN}Content: {content}{Colors.ENDC}")
                elif event_type == "done":
                    print(f"{Colors.GREEN}Stream completed{Colors.ENDC}")
                elif event_type == "error":
                    print(f"{Colors.RED}Error: {data.get('content')}{Colors.ENDC}")
                else:
                    print(f"{Colors.CYAN}Raw data:{Colors.ENDC}")
                    print(f"  {json.dumps(data, ensure_ascii=False, indent=2)}")
                    
            except json.JSONDecodeError as e:
                print(f"{Colors.RED}JSON parse error: {e}{Colors.ENDC}")
                print(f"{Colors.DIM}Raw: {json_str[:200]}...{Colors.ENDC}")
        else:
            print(f"{Colors.DIM}Other: {raw_line[:100]}...{Colors.ENDC}")

    log_raw("```")
    log_raw("")
    log_raw("## Summary")
    log_raw(f"- Total events: {event_count}")
    log_raw(f"- Thinking content length: {len(thinking_content)} chars")
    log_raw(f"- Text content length: {len(text_content)} chars")
    if thinking_content:
        log_raw(f"- Thinking content: {thinking_content[:500]}")
    if text_content:
        log_raw(f"- Text content: {text_content[:500]}")

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Test Results{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    print(f"Total events: {event_count}")
    print(f"Thinking content length: {len(thinking_content)} chars")
    print(f"Text content length: {len(text_content)} chars")
    
    if thinking_content:
        print(f"\n{Colors.DIM}Thinking:{Colors.ENDC}")
        print(thinking_content[:500] + ("..." if len(thinking_content) > 500 else ""))
    
    if text_content:
        print(f"\n{Colors.GREEN}Response:{Colors.ENDC}")
        print(text_content[:500] + ("..." if len(text_content) > 500 else ""))

    print(f"\n{Colors.CYAN}Raw output saved to: {output_file}{Colors.ENDC}")

    return event_count > 0


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
        for line in process.stdout:
            print(f"{Colors.DIM}[backend] {line.rstrip()}{Colors.ENDC}")

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
    parser = argparse.ArgumentParser(description="E2E Stream Test")
    parser.add_argument("--no-server", action="store_true", help="Do not start backend server")
    parser.add_argument("--question", "-q", default="你好", help="Question to ask")
    parser.add_argument("--user-id", "-u", type=int, default=99999, help="User ID")
    parser.add_argument("--output", "-o", default=None, help="Output file path")
    args = parser.parse_args()

    timestamp = get_timestamp()
    output_file = args.output or str(Path(__file__).parent / f"stream_output_{timestamp}.md")

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

        success = await run_test(args.question, args.user_id, output_file)

        if success:
            print(f"\n{Colors.GREEN}Test passed!{Colors.ENDC}")
            return 0
        else:
            print(f"\n{Colors.RED}Test failed{Colors.ENDC}")
            return 1

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted{Colors.ENDC}")
        return 130
    finally:
        if started_backend and backend_process is not None:
            stop_backend(backend_process)


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
