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
    return Path(__file__).resolve().parents[4]


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


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查指定端口是否已被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def start_backend() -> Optional[subprocess.Popen]:
    backend_dir = Path(__file__).parent.parent.parent.resolve()
    workspace_root = backend_dir.parent  # WorkBranch/
    project_root = workspace_root.parent  # agentb/

    # [FIX] 检查端口是否已被占用，如果已被占用则不启动新进程
    if is_port_in_use(8000):
        print(f"{Colors.YELLOW}[WARN] Port 8000 already in use - will use existing backend{Colors.ENDC}")
        return None

    python_executable = str(Path(sys.executable))
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        python_executable = str(venv_python)
        print(f"{Colors.CYAN}Using virtual environment: {python_executable}{Colors.ENDC}")

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


def start_mock_servers() -> List[subprocess.Popen]:
    """启动所有 mock 服务器（AI Judgment 和 Facility Report）"""
    tools_dir = Path(__file__).parent.parent.parent / "service" / "agent_service" / "tools"
    processes = []

    mock_servers = [
        ("ai_judgment_mock_server.py", 8080),
        ("facility_report_mock_server.py", 8001),
    ]

    print(f"\n{Colors.CYAN}Starting mock servers...{Colors.ENDC}")

    for script_name, port in mock_servers:
        script_path = tools_dir / script_name
        if not script_path.exists():
            print(f"{Colors.YELLOW}Mock server not found: {script_path}{Colors.ENDC}")
            continue

        # 检查端口是否已被占用，如果是则终止占用进程
        import socket
        for _ in range(3):  # 最多重试3次
            try:
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_sock.settimeout(1)
                result = test_sock.connect_ex(('localhost', port))
                test_sock.close()
                if result != 0:
                    # 端口未被占用
                    break
                # 端口被占用，尝试终止
                print(f"{Colors.YELLOW}Port {port} is in use, attempting to free it...{Colors.ENDC}")
                if os.name == "nt":
                    import subprocess as sp
                    try:
                        # 查找并终止占用端口的进程
                        result = sp.run(
                            f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr :{port} ^| findstr LISTENING\') do @taskkill /F /PID %a',
                            shell=True, capture_output=True, text=True, encoding='utf-8', errors='replace'
                        )
                        if result.returncode == 0:
                            print(f"{Colors.DIM}Terminated process on port {port}{Colors.ENDC}")
                    except Exception as e:
                        print(f"{Colors.DIM}Could not terminate process: {e}{Colors.ENDC}")
                time.sleep(0.5)
            except Exception:
                break

        command = [sys.executable, str(script_path), "--port", str(port)]
        print(f"{Colors.CYAN}Starting {script_name} on port {port}...{Colors.ENDC}")

        kwargs = {
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

        try:
            proc = subprocess.Popen(command, **kwargs)

            # 等待一小段时间检查进程是否启动
            time.sleep(0.5)

            if proc.poll() is not None:
                # 进程已退出，读取错误信息
                try:
                    stdout, _ = proc.communicate(timeout=1)
                    print(f"{Colors.RED}Mock server {script_name} failed to start:{Colors.ENDC}")
                    print(f"{Colors.RED}{stdout or 'Unknown error'}{Colors.ENDC}")
                except:
                    print(f"{Colors.RED}Mock server {script_name} failed to start{Colors.ENDC}")
                continue

            def stream_output(proc_ref, name):
                assert proc_ref.stdout is not None
                for line in proc_ref.stdout:
                    print(f"{Colors.DIM}[{name}] {line.rstrip()}{Colors.ENDC}")

            thread = threading.Thread(target=stream_output, args=(proc, script_name.replace("_mock_server.py", "").upper()), daemon=True)
            thread.start()

            processes.append(proc)
            print(f"{Colors.GREEN}Mock server started: {script_name} (PID: {proc.pid}){Colors.ENDC}")
        except Exception as e:
            print(f"{Colors.RED}Failed to start {script_name}: {e}{Colors.ENDC}")

    # 等待服务器启动
    time.sleep(1)

    print(f"{Colors.GREEN}Mock servers ready: {len(processes)}/{len(mock_servers)}{Colors.ENDC}\n")

    return processes


def stop_mock_servers(processes: List[subprocess.Popen]):
    """停止所有 mock 服务器"""
    if not processes:
        return

    print(f"\n{Colors.CYAN}Stopping mock servers...{Colors.ENDC}")

    for proc in processes:
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    print(f"{Colors.GREEN}Mock servers stopped{Colors.ENDC}")


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
        self.next_conversation_id: Optional[str] = None
        self.raw_lines: List[str] = []
        self.done = False
        self.response_text = ""
        # [方案B] 工作区轮询结果
        self.workspace_files_checked: bool = False
        self.prediction_report_found: bool = False
        self.prediction_report_name: Optional[str] = None
        self.prediction_report_size: int = 0
        # 健康评级比对相关字段
        self.ground_truth_grade: Optional[str] = None
        self.predicted_grade: Optional[str] = None
        self.predicted_bci: Optional[float] = None
        self.grade_comparison: Optional[Dict] = None
        self.grade_score: int = 0

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
            "workspace_files_checked": self.workspace_files_checked,
            "prediction_report_found": self.prediction_report_found,
            "prediction_report_name": self.prediction_report_name,
            "prediction_report_size": self.prediction_report_size,
            # 健康评级比对
            "ground_truth_grade": self.ground_truth_grade,
            "predicted_grade": self.predicted_grade,
            "predicted_bci": self.predicted_bci,
            "grade_comparison": self.grade_comparison,
            "grade_score": self.grade_score,
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
            async with httpx.AsyncClient(timeout=300.0) as client:
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
        
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, read=30.0))
        try:
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
        finally:
            await client.aclose()


async def wait_for_conversation_state(
    api: APIClient,
    conversation_id: str,
    expected_state: str,
    timeout: float = 120.0,
    poll_interval: float = 1.0,
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
        if expected_state == "processing" and current_state == "completed":
            return conversation_result
        
        await asyncio.sleep(poll_interval)
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
    timeout: float = 300.0,
    stream_log_file: Optional[str] = None,
):
    import asyncio

    # [FIX] 事件去重：跟踪已处理的事件标识符
    seen_event_keys: set = set()
    duplicate_count: int = 0

    deadline = time.time() + timeout
    max_retries = 3
    retry_count = 0
    retry_delay = 2.0
    
    stream_log_fh = None
    if stream_log_file:
        try:
            log_path = Path(stream_log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stream_log_fh = open(log_path, "w", encoding="utf-8")
            
            header = "=" * 80 + "\n"
            header += f"E2E Stream Trace Log\n"
            header += f"Conversation ID: {conversation_id}\n"
            header += f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            header += "=" * 80 + "\n\n"
            
            _write_stream_log(stream_log_fh, header)
        except Exception as e:
            print(f"{Colors.YELLOW}[stream_log] Failed to open log file: {str(e)}{Colors.ENDC}")
            stream_log_fh = None
    
    while retry_count <= max_retries and time.time() < deadline:
        try:
            stream_iter = api.stream_message(conversation_id, use_v2=use_v2)
            pending_item = None
            loop_count = 0
            consecutive_timeouts = 0
            max_consecutive_timeouts = 30
            
            while time.time() < deadline:
                loop_count += 1
                if verbose and loop_count % 20 == 0:
                    elapsed = time.time() - (deadline - timeout)
                    print(f"{Colors.DIM}[loop {loop_count}] waiting for stream... ({elapsed:.0f}s elapsed){Colors.ENDC}")
                
                try:
                    if pending_item is None:
                        pending_item = asyncio.create_task(stream_iter.__anext__())
                    
                    done, _ = await asyncio.wait(
                        {pending_item},
                        timeout=1.0,
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    if not done:
                        consecutive_timeouts += 1
                        
                        if verbose and consecutive_timeouts % 10 == 0:
                            print(f"{Colors.DIM}[wait timeout] checking conversation state... ({consecutive_timeouts}s idle){Colors.ENDC}")
                        
                        if consecutive_timeouts >= max_consecutive_timeouts:
                            if verbose:
                                print(f"{Colors.YELLOW}[warn] Stream idle for {consecutive_timeouts}s, checking state...{Colors.ENDC}")
                            conv_check = await api.get_conversation(conversation_id)
                            conv_state = conv_check.get("data", {}).get("state")
                            if verbose:
                                print(f"{Colors.DIM}[idle check] state={conv_state}{Colors.ENDC}")
                            if conv_state == "completed":
                                if verbose:
                                    print(f"{Colors.GREEN}[idle] Conversation completed, ending stream{Colors.ENDC}")
                                result.done = True
                                pending_item.cancel()
                                return
                            consecutive_timeouts = 0
                        continue
                    
                    item = await pending_item
                    pending_item = None
                    consecutive_timeouts = 0
                    
                except StopAsyncIteration:
                    if verbose:
                        print(f"{Colors.GREEN}[StopAsyncIteration] Stream ended{Colors.ENDC}")
                    return
                except Exception as e:
                    error_str = str(e)
                    if verbose:
                        print(f"{Colors.RED}[stream error] {error_str}{Colors.ENDC}")
                    
                    fatal_errors = ["404", "403", "401", "not found", "unauthorized"]
                    if any(err in error_str.lower() for err in fatal_errors):
                        if verbose:
                            print(f"{Colors.RED}[fatal] Fatal error, stopping{Colors.ENDC}")
                        return
                    
                    retry_count += 1
                    if retry_count > max_retries:
                        if verbose:
                            print(f"{Colors.RED}[retry exhausted] Max retries reached{Colors.ENDC}")
                        return
                    
                    if verbose:
                        print(f"{Colors.YELLOW}[retry] Attempt {retry_count}/{max_retries} after {retry_delay}s...{Colors.ENDC}")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    break
                
                if item is None:
                    if verbose:
                        print(f"{Colors.YELLOW}[warning] Received None from stream iterator, skipping{Colors.ENDC}")
                    continue
                
                if not isinstance(item, dict):
                    if verbose:
                        print(f"{Colors.YELLOW}[warning] Invalid item type: {type(item).__name__}, skipping{Colors.ENDC}")
                    continue
                
                raw_line = item.get("raw_line", "")
                if not raw_line or not isinstance(raw_line, str):
                    continue
                if not raw_line.strip():
                    continue

                # [DEBUG] 打印所有收到的原始行
                if verbose:
                    print(f"{Colors.DIM}[RAW-RECV] {raw_line[:200]}{Colors.ENDC}")

                if show_raw:
                    result.raw_lines.append(raw_line)
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"{Colors.DIM}[RAW {timestamp}] {raw_line}{Colors.ENDC}")

                if raw_line.startswith(": heartbeat"):
                    _write_stream_log(stream_log_fh, f"[{_get_timestamp_ms()}] [HEARTBEAT]\n")
                    if verbose and loop_count % 15 == 0:
                        print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
                    try:
                        conv_check = await api.get_conversation(conversation_id)
                        if conv_check and isinstance(conv_check, dict):
                            conv_data = conv_check.get("data", {})
                            if isinstance(conv_data, dict):
                                conv_state = conv_data.get("state")
                                if conv_state == "completed":
                                    if verbose:
                                        print(f"{Colors.GREEN}[heartbeat] Conversation completed{Colors.ENDC}")
                                    result.done = True
                                    return
                    except Exception as heartbeat_err:
                        if verbose:
                            print(f"{Colors.DIM}[heartbeat error] {heartbeat_err}{Colors.ENDC}")
                    continue

                if not raw_line.startswith("data: "):
                    continue

                try:
                    data = json.loads(raw_line[6:])
                except json.JSONDecodeError:
                    continue

                # [FIX] 事件去重：根据 type + seq/id 组合去重
                # 后端MQ有多订阅者，会发送重复事件，这里过滤掉重复事件
                event_type = data.get("type", "unknown")
                event_key = data.get("seq") or data.get("id") or data.get("event_id") or f"{event_type}_{data.get('content', '')[:50]}"
                dedup_key = f"{event_type}:{event_key}"
                if dedup_key in seen_event_keys:
                    duplicate_count += 1
                    if verbose and result.event_count < 5:
                        print(f"{Colors.DIM}[dedup] Skipping duplicate {event_type} (key={event_key}){Colors.ENDC}")
                    continue
                seen_event_keys.add(dedup_key)

                result.event_count += 1
                timestamp = _get_timestamp_ms()
                
                _write_stream_log(stream_log_fh, f"[{timestamp}] [SEQ:{result.event_count:04d}] [{event_type}]\n")

                if event_type == "text_delta":
                    content = data.get("content", "")
                    result.text_content += content
                    _write_stream_log(stream_log_fh, f"  Content: {content}\n")
                    if verbose:
                        safe_print(f"{Colors.CYAN}[text] {content}{Colors.ENDC}")
                elif event_type == "chat_delta":
                    content = data.get("content", "")
                    result.chat_content += content
                    _write_stream_log(stream_log_fh, f"  Content: {content}\n")
                    if verbose:
                        safe_print(f"{Colors.GREEN}[chat] {content}{Colors.ENDC}")
                elif event_type == "chat_end":
                    result.done = True
                    _write_stream_log(stream_log_fh, f"  → Chat completed ✓\n")
                    if verbose:
                        print(f"{Colors.GREEN}[chat_end] Chat completed{Colors.ENDC}")
                    return
                elif event_type == "thinking_delta":
                    content = data.get("content", "")
                    result.thinking_content += content
                    preview = content[:100] + ("..." if len(content) > 100 else "")
                    _write_stream_log(stream_log_fh, f"  Thinking: {preview}\n")
                    if verbose and len(content) > 10:
                        safe_print(f"{Colors.DIM}[thinking] {content[:50]}...{Colors.ENDC}")
                elif event_type == "tool_call":
                    metadata = data.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        metadata = {}
                    tool_name = metadata.get("tool_name", "unknown")
                    result.tool_calls.append(tool_name)
                    args_preview = str(metadata.get("tool_args", {}))[:150]
                    _write_stream_log(stream_log_fh, f"  Tool: {tool_name}({args_preview})\n")
                    if verbose:
                        args_preview_short = args_preview[:80]
                        print(f"{Colors.MAGENTA}[tool_call] {tool_name}({args_preview_short}){Colors.ENDC}")
                elif event_type == "state_change":
                    metadata = data.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        metadata = {}
                    execution_mode = metadata.get("execution_mode")
                    plan_status = metadata.get("plan_status")
                    log_line = f"  State: mode={execution_mode}, plan={plan_status}"
                    _write_stream_log(stream_log_fh, log_line + "\n")
                    
                    if execution_mode:
                        result.detected_mode = execution_mode
                        if execution_mode not in result.detected_modes:
                            result.detected_modes.append(execution_mode)
                        if verbose:
                            print(f"{Colors.YELLOW}[state] execution_mode: {execution_mode}{Colors.ENDC}")
                    if plan_status:
                        result.plan_status = plan_status
                        if verbose:
                            print(f"{Colors.YELLOW}[state] plan_status: {plan_status}{Colors.ENDC}")
                elif event_type == "plan_start":
                    _write_stream_log(stream_log_fh, f"  → Plan generation started\n")
                    if verbose:
                        print(f"{Colors.YELLOW}[plan_start] Plan generation started{Colors.ENDC}")
                elif event_type == "plan_delta":
                    content = data.get("content", "")
                    preview = content[:150] + ("..." if len(content) > 150 else "")
                    _write_stream_log(stream_log_fh, f"  Plan delta: {preview}\n")
                    if verbose:
                        print(f"{Colors.YELLOW}[plan] {content[:50]}...{Colors.ENDC}")
                elif event_type == "plan_end":
                    _write_stream_log(stream_log_fh, f"  → Plan generation completed\n")
                    if verbose:
                        print(f"{Colors.YELLOW}[plan_end] Plan generation completed{Colors.ENDC}")
                elif event_type == "conversation_handoff":
                    metadata = data.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        metadata = {}
                    auto_approved = metadata.get('auto_approved')
                    next_conv_id = metadata.get('next_conversation_id')
                    
                    if auto_approved and next_conv_id:
                        result.next_conversation_id = next_conv_id
                    
                    _write_stream_log(stream_log_fh, f"  Handoff: approved={auto_approved}, next={next_conv_id}\n")
                    if verbose:
                        print(f"{Colors.HEADER}[conversation_handoff] auto_approved: {auto_approved}, next_conversation_id: {next_conv_id}{Colors.ENDC}")
                elif event_type == "done":
                    result.done = True
                    _write_stream_log(stream_log_fh, f"  → Stream completed ✓\n")
                    if verbose:
                        print(f"{Colors.GREEN}[done] Stream completed{Colors.ENDC}")
                    return
                elif event_type == "error":
                    error_content = data.get("content", "Unknown error")
                    result.errors.append(error_content)
                    _write_stream_log(stream_log_fh, f"  ✗ ERROR: {error_content}\n")
                    if verbose:
                        safe_print(f"{Colors.RED}[error] {error_content}{Colors.ENDC}")
                else:
                    preview = json.dumps(data, ensure_ascii=False)[:200]
                    _write_stream_log(stream_log_fh, f"  Raw: {preview}\n")
                    if verbose:
                        print(f"{Colors.BLUE}[{event_type}] {preview}...{Colors.ENDC}")
            
            if time.time() >= deadline:
                if verbose:
                    print(f"{Colors.YELLOW}[timeout] Stream collection timeout after {timeout:.0f}s{Colors.ENDC}")
                return
        
        except Exception as e:
            retry_count += 1
            if retry_count > max_retries:
                if verbose:
                    print(f"{Colors.RED}[connection failed] Max retries exceeded: {str(e)}{Colors.ENDC}")
                return
            
            if verbose:
                print(f"{Colors.YELLOW}[reconnect] Connection error, retry {retry_count}/{max_retries}: {str(e)}{Colors.ENDC}")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2
    
    if verbose:
        elapsed = time.time() - (deadline - timeout)
        print(f"{Colors.DIM}[completed] Stream collection ended after {elapsed:.0f}s, events={result.event_count}, tools={result.tool_calls}, duplicates_skipped={duplicate_count}{Colors.ENDC}")
    
    if stream_log_fh and not stream_log_fh.closed:
        footer = "\n" + "=" * 80 + "\n"
        footer += f"Ended: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        footer += f"Total Events: {result.event_count} | Tools: {result.tool_calls}\n"
        footer += f"Duration: {elapsed:.1f}s\n"
        footer += "=" * 80 + "\n"
        
        try:
            _write_stream_log(stream_log_fh, footer)
            stream_log_fh.close()
            print(f"{Colors.GREEN}[stream_log] Log saved: {stream_log_file} ({result.event_count} events){Colors.ENDC}")
        except Exception as e:
            print(f"{Colors.YELLOW}[stream_log] Error closing log: {str(e)}{Colors.ENDC}")


def _write_stream_log(fh, content: str):
    """辅助函数：安全写入流式日志"""
    if fh is None or fh.closed:
        return
    try:
        fh.write(content)
        fh.flush()
    except Exception as e:
        pass


def _get_timestamp_ms():
    """获取带毫秒精度的时间戳字符串（兼容 Windows）"""
    now = time.time()
    sec = int(now)
    ms = int((now - sec) * 1000)
    return time.strftime("%H:%M:%S", time.localtime(sec)) + f".{ms:03d}"


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


def print_warning(message: str):
    print(f"{Colors.YELLOW}    {message}{Colors.ENDC}")
