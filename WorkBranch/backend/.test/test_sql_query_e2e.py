#!/usr/bin/env python3
"""
SQL Query Tool E2E Test

测试目标:
1. 校验项目根目录 setting.json 已放通 sql_query
2. 创建独立的测试数据库并插入测试数据
3. 临时写入 sql_tools_config.json，让 SQL 工具连接测试库
4. 创建对话，让 agent 在 DIRECT 模式下调用 sql_query 做查询与统计
5. 验证流式执行完成、尽量触发 sql_query、多轮查询后输出正确统计结论

Usage:
    python test_sql_query_e2e.py [--no-server]
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
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "setting.json"
SQL_TOOL_CONFIG_PATH = BACKEND_DIR / "service" / "agent_service" / "tools" / "sql_tools_config.json"

TEST_ROWS = [
    {"customer_name": "Alice", "category": "electronics", "amount": 120, "status": "paid"},
    {"customer_name": "Bob", "category": "books", "amount": 35, "status": "paid"},
    {"customer_name": "Cara", "category": "electronics", "amount": 80, "status": "pending"},
    {"customer_name": "Dan", "category": "books", "amount": 20, "status": "paid"},
    {"customer_name": "Eve", "category": "grocery", "amount": 15, "status": "paid"},
    {"customer_name": "Frank", "category": "grocery", "amount": 40, "status": "cancelled"},
]

EXPECTED_TOTAL_ORDERS = len(TEST_ROWS)
EXPECTED_PAID_ORDERS = sum(1 for row in TEST_ROWS if row["status"] == "paid")
EXPECTED_PAID_TOTAL = sum(row["amount"] for row in TEST_ROWS if row["status"] == "paid")
EXPECTED_PAID_CATEGORY_TOTALS = {
    category: sum(row["amount"] for row in TEST_ROWS if row["status"] == "paid" and row["category"] == category)
    for category in sorted({row["category"] for row in TEST_ROWS})
}


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
        self.tool_results: List[dict] = []
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
            "tool_results": self.tool_results,
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


def load_json_file(file_path: Path) -> dict:
    return json.loads(file_path.read_text(encoding="utf-8"))


def ensure_sql_query_enabled(settings: dict) -> list[str]:
    missing = []
    tool_permissions = settings.get("tool_permissions") or {}
    for agent_type in ["director_agent", "plan_agent", "review_agent", "explore_agent", "admin_agent"]:
        allowed = ((tool_permissions.get(agent_type) or {}).get("allowed") or [])
        if "sql_query" not in allowed:
            missing.append(agent_type)
    return missing


def get_sql_connection_settings() -> dict:
    settings = load_json_file(SETTINGS_PATH)
    mysql_settings = settings.get("mysql") or {}
    return {
        "host": os.environ.get("SQL_E2E_MYSQL_HOST", mysql_settings.get("host", "localhost")),
        "port": int(os.environ.get("SQL_E2E_MYSQL_PORT", mysql_settings.get("port", 3306))),
        "user": os.environ.get("SQL_E2E_MYSQL_USER", mysql_settings.get("user", "root")),
        "password": os.environ.get("SQL_E2E_MYSQL_PASSWORD", mysql_settings.get("password", "")),
        "charset": os.environ.get("SQL_E2E_MYSQL_CHARSET", "utf8mb4"),
    }


async def create_test_database(connection_settings: dict) -> tuple[str, dict]:
    try:
        import aiomysql
    except ImportError as e:
        raise RuntimeError(f"缺少 aiomysql 依赖: {e}") from e

    db_name = f"agentb_sql_e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}".lower()

    conn = await aiomysql.connect(
        host=connection_settings["host"],
        port=connection_settings["port"],
        user=connection_settings["user"],
        password=connection_settings["password"],
        charset=connection_settings["charset"],
        autocommit=True,
    )
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        conn.close()

    seeded_conn = await aiomysql.connect(
        host=connection_settings["host"],
        port=connection_settings["port"],
        user=connection_settings["user"],
        password=connection_settings["password"],
        db=db_name,
        charset=connection_settings["charset"],
        autocommit=True,
    )
    try:
        async with seeded_conn.cursor() as cursor:
            await cursor.execute(
                """
                CREATE TABLE orders (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    customer_name VARCHAR(64) NOT NULL,
                    category VARCHAR(32) NOT NULL,
                    amount INT NOT NULL,
                    status VARCHAR(16) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            await cursor.executemany(
                "INSERT INTO orders (customer_name, category, amount, status) VALUES (%s, %s, %s, %s)",
                [(row["customer_name"], row["category"], row["amount"], row["status"]) for row in TEST_ROWS],
            )
    finally:
        seeded_conn.close()

    expected = {
        "database": db_name,
        "total_orders": EXPECTED_TOTAL_ORDERS,
        "paid_orders": EXPECTED_PAID_ORDERS,
        "paid_total": EXPECTED_PAID_TOTAL,
        "paid_category_totals": EXPECTED_PAID_CATEGORY_TOTALS,
    }
    return db_name, expected


async def drop_test_database(connection_settings: dict, db_name: Optional[str]):
    if not db_name:
        return

    try:
        import aiomysql
    except ImportError:
        return

    conn = await aiomysql.connect(
        host=connection_settings["host"],
        port=connection_settings["port"],
        user=connection_settings["user"],
        password=connection_settings["password"],
        charset=connection_settings["charset"],
        autocommit=True,
    )
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
    finally:
        conn.close()


def write_sql_tool_config(connection_settings: dict, db_name: str):
    config_payload = {
        "default_database": db_name,
        "databases": {
            db_name: {
                "host": connection_settings["host"],
                "port": connection_settings["port"],
                "user": connection_settings["user"],
                "password": connection_settings["password"],
                "charset": connection_settings["charset"],
            }
        },
    }
    SQL_TOOL_CONFIG_PATH.write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def build_prompt(db_name: str) -> str:
    return (
        f"请只使用 sql_query 工具完成数据库统计，禁止猜测，禁止写文件。"
        f"sql_query 的参数名必须使用 query、database、limit，其中 database 固定填写 {db_name}。"
        f"当前测试数据库里有一张 orders 表，字段是 id、customer_name、category、amount、status。"
        f"请分步骤完成：1）统计总订单数；2）统计 status='paid' 的订单数；"
        f"3）统计 status='paid' 的总金额；4）统计每个 category 在 status='paid' 下的金额汇总，并按金额降序给出。"
        f"如果你觉得任务是多阶段的，可以先建立 TODO 再继续。"
        f"最后用中文输出简短结论，明确写出总订单数、paid 订单数、paid 总金额，并列出 electronics、books、grocery 三个分类的结果。"
    )


async def run_sql_query_test(api: APIClient, output_file: str, db_name: str, expected: dict) -> Dict:
    output_lines: List[str] = []
    errors: List[str] = []
    prompt = build_prompt(db_name)

    output_lines.append(f"# SQL Query Tool E2E - {get_timestamp()}")
    output_lines.append("")

    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  SQL Query Tool E2E Test{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("SQL Query Tool E2E")
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
    output_lines.append(f"- sql_database: {db_name}")
    output_lines.append("")

    output_lines.append("## Expected Statistics")
    output_lines.append(json.dumps(expected, ensure_ascii=False, indent=2))
    output_lines.append("")

    print(f"{Colors.CYAN}[Step 2] Creating conversation...{Colors.ENDC}")
    print(f"{Colors.DIM}    Prompt: {prompt}{Colors.ENDC}")
    conversation_create_result = await api.create_conversation(session_id, prompt)
    if conversation_create_result.get("code") != 200:
        error_msg = f"Conversation creation failed: {conversation_create_result.get('message', 'Unknown error')}"
        print(f"{Colors.RED}{error_msg}{Colors.ENDC}")
        return {
            "success": False,
            "errors": [error_msg],
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
                    safe_print(f"{Colors.GREEN}[chat] {content}{Colors.ENDC}")
                elif event_type == "text_delta":
                    content = data.get("content", "")
                    result.text_content += content
                    safe_print(f"{Colors.CYAN}[text] {content}{Colors.ENDC}")
                elif event_type == "tool_call":
                    metadata = data.get("metadata", {})
                    tool_name = metadata.get("tool_name", "unknown")
                    result.tool_calls.append(tool_name)
                    print(f"{Colors.MAGENTA}[tool_call] {tool_name} {json.dumps(metadata.get('tool_args', {}), ensure_ascii=False)}{Colors.ENDC}")
                elif event_type == "tool_res":
                    metadata = data.get("metadata", {})
                    result.tool_results.append(metadata)
                    print(f"{Colors.BLUE}[tool_res] {json.dumps(metadata, ensure_ascii=False)[:200]}{Colors.ENDC}")
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
                    print(f"{Colors.BLUE}[{event_type}] {json.dumps(data, ensure_ascii=False)[:160]}...{Colors.ENDC}")
            except json.JSONDecodeError as e:
                parse_error = f"JSON parse error: {e}"
                result.errors.append(parse_error)
                print(f"{Colors.RED}{parse_error}{Colors.ENDC}")

    output_lines.append("```")
    output_lines.append("")

    print(f"\n{Colors.CYAN}[Step 4] Waiting for completed state...{Colors.ENDC}")
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

    sql_query_calls = [tool for tool in result.tool_calls if tool == "sql_query"]
    if not sql_query_calls:
        errors.append(f"sql_query was not observed in tool calls: {result.tool_calls}")
    elif len(sql_query_calls) < 2:
        errors.append(f"Expected multiple sql_query calls, got: {len(sql_query_calls)}")

    if "write_file" in result.tool_calls:
        errors.append(f"write_file should not be used in this SQL read-only test: {result.tool_calls}")

    response_text = result.response_text
    for expected_text in [
        str(expected["total_orders"]),
        str(expected["paid_orders"]),
        str(expected["paid_total"]),
        "electronics",
        "books",
        "grocery",
    ]:
        if expected_text not in response_text:
            errors.append(f"Final response missing expected text: {expected_text}; response={response_text}")

    output_lines.append("## Final Errors")
    output_lines.append(json.dumps(errors, ensure_ascii=False, indent=2))
    output_lines.append("")

    Path(output_file).write_text("\n".join(output_lines), encoding="utf-8")
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
    parser = argparse.ArgumentParser(description="SQL Query Tool E2E Test")
    parser.add_argument("--no-server", action="store_true", help="Do not start server automatically")
    parser.add_argument("--user-id", type=int, default=1, help="User ID for API requests")
    args = parser.parse_args()

    backend_process = None
    started_backend = False
    db_name = None
    original_sql_tool_config = SQL_TOOL_CONFIG_PATH.read_text(encoding="utf-8") if SQL_TOOL_CONFIG_PATH.exists() else None
    connection_settings = get_sql_connection_settings()

    try:
        settings = load_json_file(SETTINGS_PATH)
        missing_agents = ensure_sql_query_enabled(settings)
        if missing_agents:
            print(f"{Colors.RED}setting.json 未放通 sql_query: {missing_agents}{Colors.ENDC}")
            return 1
        print(f"{Colors.GREEN}setting.json 已放通 sql_query{Colors.ENDC}")

        print(f"{Colors.CYAN}Preparing isolated SQL test database...{Colors.ENDC}")
        db_name, expected = await create_test_database(connection_settings)
        write_sql_tool_config(connection_settings, db_name)
        print(f"{Colors.GREEN}Test database ready: {db_name}{Colors.ENDC}")

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
        output_file = logs_dir / f"sql_query_e2e_{get_timestamp()}.md"

        result = await run_sql_query_test(api, str(output_file), db_name, expected)
        return 0 if result.get("success") else 1

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted{Colors.ENDC}")
        return 130
    finally:
        if started_backend and backend_process is not None:
            stop_backend(backend_process)

        try:
            if original_sql_tool_config is not None:
                SQL_TOOL_CONFIG_PATH.write_text(original_sql_tool_config, encoding="utf-8")
        except Exception as e:
            print(f"{Colors.YELLOW}Restore sql_tools_config warning: {e}{Colors.ENDC}")

        try:
            await drop_test_database(connection_settings, db_name)
        except Exception as e:
            print(f"{Colors.YELLOW}Drop test database warning: {e}{Colors.ENDC}")

        try:
            if str(BACKEND_DIR) not in sys.path:
                sys.path.insert(0, str(BACKEND_DIR))
            from singleton import clear_all_singletons_async

            await clear_all_singletons_async()
        except Exception as e:
            print(f"{Colors.YELLOW}Cleanup warning: {e}{Colors.ENDC}")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
