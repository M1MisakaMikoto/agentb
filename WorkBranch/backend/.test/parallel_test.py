#!/usr/bin/env python3
"""
Parallel E2E Test for AgentB Backend

Test Flow:
1. Create Session (auto-create user via X-User-ID)
2. Create Conversation
3. Send message and receive SSE stream
4. Verify response integrity and isolation

Usage:
    python parallel_test.py [--no-server]
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
LOG_DIR = Path(__file__).parent / "logs" / "parallel_test"


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


def ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class TestLogger:
    def __init__(self, process_id: str):
        self.process_id = process_id
        self.log_file = LOG_DIR / f"process_{process_id}_{get_timestamp()}.log"
        self.entries: list[dict] = []

    def log(self, event: str, **kwargs):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "process_id": self.process_id,
            "event": event,
            **kwargs,
        }
        self.entries.append(entry)

    def save(self):
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=2)


class APIClient:
    def __init__(self, base_url: str, user_id: int, logger: TestLogger):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.logger = logger

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
            except httpx.HTTPStatusError as e:
                try:
                    error_data = e.response.json()
                    return {"code": e.response.status_code, "message": error_data.get("detail", str(e)), "data": None}
                except Exception:
                    return {"code": e.response.status_code, "message": str(e), "data": None}
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
                        self.logger.log("stream_error", error=error.decode())
                    except Exception:
                        self.logger.log("stream_error", status_code=response.status_code)
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            yield data
                        except json.JSONDecodeError:
                            continue


TEST_CASES = [
    {
        "process_id": "math_user",
        "user_id": 10001,
        "theme": "数学",
        "question": "请计算 123 * 456 等于多少？请详细说明计算过程。",
        "keywords": ["56088", "计算", "乘法"],
    },
    {
        "process_id": "programming_user",
        "user_id": 10002,
        "theme": "编程",
        "question": "Python中如何实现单例模式？请给出详细说明。",
        "keywords": ["单例", "Python", "实例", "__new__", "装饰器"],
    },
    {
        "process_id": "greeting_user",
        "user_id": 10003,
        "theme": "问候",
        "question": "你好",
        "keywords": ["你好", "您好", "帮助", "助手"],
    },
]


async def run_single_test(test_case: dict) -> dict:
    process_id = test_case["process_id"]
    user_id = test_case["user_id"]
    theme = test_case["theme"]
    question = test_case["question"]
    expected_keywords = test_case["keywords"]

    logger = TestLogger(process_id)
    api = APIClient(BASE_URL, user_id, logger)

    result = {
        "process_id": process_id,
        "user_id": user_id,
        "theme": theme,
        "success": True,
        "errors": [],
        "response": None,
        "events_received": [],
    }

    logger.log("test_start", theme=theme, user_id=user_id)

    try:
        session_result = await api.create_session(f"并行测试-{theme}")
        if session_result.get("code") != 200:
            result["errors"].append(f"创建会话失败: {session_result.get('message')}")
            result["success"] = False
            logger.log("session_create_failed", message=session_result.get("message"))
            return result

        session_id = session_result["data"]["id"]
        logger.log("session_created", session_id=session_id)

        conv_result = await api.create_conversation(session_id, question)
        if conv_result.get("code") != 200:
            result["errors"].append(f"创建对话失败: {conv_result.get('message')}")
            result["success"] = False
            logger.log("conversation_create_failed", message=conv_result.get("message"))
            return result

        conversation_id = conv_result["data"]["conversation_id"]
        logger.log("conversation_created", conversation_id=conversation_id)

        full_response = ""
        thinking_content = ""
        text_content = ""
        event_types_received = []

        async for event in api.stream_message(conversation_id):
            event_type = event.get("type")
            event_types_received.append(event_type)

            if event_type == "thinking_delta":
                thinking_content += event.get("content", "")
            elif event_type == "text_delta":
                text_content += event.get("content", "")
            elif event_type == "text_end":
                logger.log("response_complete", text_length=len(text_content))
            elif event_type == "thinking_end":
                logger.log("thinking_complete", thinking_length=len(thinking_content))
            elif event_type == "done":
                logger.log("stream_done")
            elif event_type == "error":
                result["errors"].append(f"流式响应错误: {event.get('content')}")
                logger.log("stream_error", error=event.get("content"))

        result["events_received"] = event_types_received
        logger.log("events_received", event_types=event_types_received)

        full_response = text_content if text_content else thinking_content
        result["response"] = full_response[:500] if len(full_response) > 500 else full_response

        found_keywords = []
        missing_keywords = []
        for keyword in expected_keywords:
            if keyword in full_response:
                found_keywords.append(keyword)
            else:
                missing_keywords.append(keyword)

        if missing_keywords:
            logger.log(
                "keyword_check_warning",
                missing_keywords=missing_keywords,
                found_keywords=found_keywords,
            )
        else:
            logger.log("keyword_check_passed", found_keywords=found_keywords)

        other_themes_keywords = []
        for other_case in TEST_CASES:
            if other_case["process_id"] != process_id:
                other_themes_keywords.extend(other_case["keywords"])

        contamination = []
        for keyword in other_themes_keywords:
            if keyword in full_response and keyword not in expected_keywords:
                contamination.append(keyword)

        if contamination:
            result["errors"].append(f"检测到可能的混杂内容: {contamination}")
            logger.log("contamination_detected", contamination=contamination)
        else:
            logger.log("no_contamination")

        if "done" not in event_types_received:
            result["errors"].append("未收到 done 事件，流式响应可能不完整")
            result["success"] = False

        logger.log("test_complete", success=result["success"], error_count=len(result["errors"]))

    except Exception as e:
        result["success"] = False
        result["errors"].append(f"测试异常: {str(e)}")
        logger.log("test_exception", error=str(e))

    logger.save()
    return result


async def run_parallel_tests() -> list[dict]:
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  并行E2E测试开始 - {get_timestamp()}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    tasks = [run_single_test(test_case) for test_case in TEST_CASES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            processed_results.append(
                {"process_id": TEST_CASES[i]["process_id"], "success": False, "errors": [str(result)], "response": None}
            )
        else:
            processed_results.append(result)

    return processed_results


def generate_report(results: list[dict]) -> str:
    lines = [
        f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}",
        f"{Colors.HEADER}  并行E2E测试报告{Colors.ENDC}",
        f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n",
    ]

    total = len(results)
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = total - success_count

    lines.append(f"总测试数: {total}")
    lines.append(f"{Colors.GREEN}成功: {success_count}{Colors.ENDC}")
    lines.append(f"{Colors.RED}失败: {fail_count}{Colors.ENDC}\n")

    lines.append("```mermaid")
    lines.append("flowchart LR")
    for result in results:
        process_id = result.get("process_id", "unknown")
        success = result.get("success", False)
        status = "✅" if success else "❌"
        lines.append(f"    {process_id}[{process_id} {status}]")
    lines.append("```\n")

    for result in results:
        process_id = result.get("process_id", "unknown")
        user_id = result.get("user_id", "?")
        theme = result.get("theme", "unknown")
        success = result.get("success", False)
        errors = result.get("errors", [])
        events = result.get("events_received", [])
        response = result.get("response", "")

        status_color = Colors.GREEN if success else Colors.RED
        status_text = "✅ 成功" if success else "❌ 失败"

        lines.append(f"{status_color}【{process_id}】{status_text}{Colors.ENDC}")
        lines.append(f"  用户ID: {user_id}")
        lines.append(f"  主题: {theme}")
        lines.append(f"  事件序列: {events}")

        if errors:
            lines.append(f"  {Colors.RED}错误:{Colors.ENDC}")
            for error in errors:
                lines.append(f"    - {error}")

        if response:
            lines.append(f"  响应摘要: {response[:200]}...")

        lines.append("")

    lines.append(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")
    lines.append(f"测试日志目录: {LOG_DIR}")
    lines.append(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")

    return "\n".join(lines)


def wait_for_backend(host: str = "127.0.0.1", port: int = 8000, timeout: float = 30.0) -> bool:
    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout

    print(f"{Colors.CYAN}等待后端服务启动...{Colors.ENDC}")

    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    print(f"{Colors.GREEN}后端服务已就绪{Colors.ENDC}")
                    return True
        except URLError:
            pass
        time.sleep(0.5)

    print(f"{Colors.RED}后端服务启动超时{Colors.ENDC}")
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

    print(f"{Colors.CYAN}启动后端服务...{Colors.ENDC}")
    print(f"{Colors.DIM}命令: {' '.join(command)}{Colors.ENDC}")
    print(f"{Colors.DIM}目录: {backend_dir}{Colors.ENDC}")

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

    print(f"{Colors.CYAN}停止后端服务...{Colors.ENDC}")

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
        print(f"{Colors.YELLOW}强制终止后端服务{Colors.ENDC}")
        process.kill()
        process.wait(timeout=5)

    print(f"{Colors.GREEN}后端服务已停止{Colors.ENDC}")


async def main():
    parser = argparse.ArgumentParser(description="Parallel E2E Test for AgentB Backend")
    parser.add_argument("--no-server", action="store_true", help="Do not start backend server (assume already running)")
    args = parser.parse_args()

    ensure_log_dir()

    backend_process = None
    started_backend = False

    try:
        if args.no_server:
            if not wait_for_backend(timeout=2):
                print(f"{Colors.RED}后端服务未运行，请先启动服务或移除 --no-server 参数{Colors.ENDC}")
                return 1
        else:
            if not wait_for_backend(timeout=2):
                print(f"{Colors.CYAN}初始化数据库连接池...{Colors.ENDC}")
                backend_dir = Path(__file__).parent.parent
                if str(backend_dir) not in sys.path:
                    sys.path.insert(0, str(backend_dir))
                from singleton import get_mysql_database
                db = await get_mysql_database()
                await db.init_tables()
                print(f"{Colors.GREEN}数据库初始化完成{Colors.ENDC}")
                
                backend_process = start_backend()
                started_backend = True

                if not wait_for_backend(timeout=120):
                    print(f"{Colors.RED}无法启动后端服务，测试终止{Colors.ENDC}")
                    return 1

        results = await run_parallel_tests()
        report = generate_report(results)
        print(report)

        report_file = LOG_DIR / f"report_{get_timestamp()}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": get_timestamp(),
                    "total": len(results),
                    "success": sum(1 for r in results if r.get("success")),
                    "failed": sum(1 for r in results if not r.get("success")),
                    "results": results,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\n{Colors.GREEN}测试报告已保存: {report_file}{Colors.ENDC}")

        if all(r.get("success") for r in results):
            print(f"\n{Colors.GREEN}所有测试通过！{Colors.ENDC}")
            return 0
        else:
            print(f"\n{Colors.RED}部分测试失败，请检查日志{Colors.ENDC}")
            return 1

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}测试被用户中断{Colors.ENDC}")
        return 130
    finally:
        if started_backend and backend_process is not None:
            stop_backend(backend_process)


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
