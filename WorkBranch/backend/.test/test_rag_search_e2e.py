#!/usr/bin/env python3
"""
RAG Search Tool E2E Test

测试目标:
1. 校验项目根目录 setting.json 已放通 rag_search
2. 创建独立的测试知识库并导入测试文档
3. 创建对话，让 agent 在 DIRECT 模式下调用 rag_search 做知识检索
4. 验证流式执行完成、触发 rag_search、输出正确检索结论

Usage:
    python test_rag_search_e2e.py [--no-server]
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import URLError
from urllib.request import urlopen

import httpx


BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "setting.json"
RAG_DIR = PROJECT_ROOT / "WorkBranch" / "rag"
RAG_META_DB = RAG_DIR / "file_meta.sqlite3"
DOCS_ROOT = PROJECT_ROOT / "DOCS"

TEST_DOCUMENTS = [
    {
        "title": "产品使用手册",
        "content": """
# 产品使用手册

## 第一章 产品概述

本产品是一款智能知识管理系统，支持文档导入、语义检索和多轮问答。

主要功能包括：
- 文档上传与管理
- 向量化存储
- 语义相似度检索
- 混合检索模式

## 第二章 快速开始

### 2.1 安装部署

系统要求 Python 3.10+，推荐使用虚拟环境。

安装步骤：
1. 克隆代码仓库
2. 安装依赖：pip install -r requirements.txt
3. 配置环境变量
4. 启动服务：python main.py

### 2.2 基本配置

配置文件位于 setting.json，主要配置项：
- database: 数据库配置
- llm: 大语言模型配置
- rag: RAG 检索配置

## 第三章 API 接口

### 3.1 文档上传接口

POST /api/documents/upload
参数：file (文件), kb_id (知识库ID)

### 3.2 检索接口

POST /api/rag/search
参数：query (查询文本), top_k (返回数量), kb_id (知识库ID)
""",
        "category": "技术文档",
    },
    {
        "title": "销售报告 2024",
        "content": """
# 销售报告 2024

## 摘要

2024年度销售总额达到 1500 万元，同比增长 25%。

## 区域销售数据

### 华东区域
- 销售额：600 万元
- 订单数：1200 单
- 客户数：350 家

### 华南区域
- 销售额：450 万元
- 订单数：900 单
- 客户数：280 家

### 华北区域
- 销售额：350 万元
- 订单数：700 单
- 客户数：200 家

### 西部区域
- 销售额：100 万元
- 订单数：200 单
- 客户数：80 家

## 产品销售排行

1. 智能助手 Pro - 销售额 500 万元
2. 知识库企业版 - 销售额 400 万元
3. API 网关 - 销售额 300 万元
4. 数据分析平台 - 销售额 200 万元
5. 其他产品 - 销售额 100 万元

## 总结与展望

2024年业绩表现优异，2025年目标增长30%。
""",
        "category": "业务报告",
    },
    {
        "title": "员工培训手册",
        "content": """
# 员工培训手册

## 公司简介

我们是一家专注于人工智能和知识管理的高科技企业，成立于2020年。

公司核心价值观：
- 创新：持续技术创新
- 客户至上：以客户需求为导向
- 团队协作：跨部门高效协作
- 追求卓越：精益求精

## 规章制度

### 工作时间
- 标准工作时间：9:00-18:00
- 弹性工作制：核心时间 10:00-16:00
- 加班需提前申请

### 请假制度
- 年假：入职满一年后享有5天年假
- 病假：凭医院证明可申请病假
- 事假：需提前3个工作日申请

### 报销流程
1. 填写报销申请单
2. 附上原始发票
3. 部门主管审批
4. 财务审核
5. 打款到工资卡

## 福利待遇

- 五险一金
- 年度体检
- 节日福利
- 团建活动
- 学习补贴
""",
        "category": "人力资源",
    },
]

EXPECTED_ANSWERS = {
    "sales_total": "1500",
    "growth_rate": "25",
    "top_product": "智能助手 Pro",
    "work_hours": "9:00-18:00",
    "annual_leave": "5天",
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
        self.tool_args_list: List[dict] = []
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
            "tool_args_list": self.tool_args_list,
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
                        error_body = await response.aread()
                        yield {"error": f"HTTP {response.status_code}: {error_body.decode()}"}
                    except Exception as e:
                        yield {"error": f"HTTP {response.status_code}: {e}"}
                    return

                async for line in response.aiter_lines():
                    yield {"raw_line": line}


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def load_json_file(file_path: Path) -> dict:
    return json.loads(file_path.read_text(encoding="utf-8"))


def ensure_rag_search_enabled(settings: dict) -> list[str]:
    missing = []
    tool_permissions = settings.get("tool_permissions") or {}
    for agent_type in ["director_agent", "plan_agent", "review_agent", "explore_agent", "admin_agent"]:
        allowed = ((tool_permissions.get(agent_type) or {}).get("allowed") or [])
        if "rag_search" not in allowed:
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
        time.sleep(1)
    return False


def start_backend() -> subprocess.Popen:
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


async def create_test_knowledge_base() -> tuple[int, dict]:
    """创建测试知识库并导入测试文档"""
    import sqlite3
    
    RAG_META_DB.parent.mkdir(parents=True, exist_ok=True)
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(RAG_META_DB)
    conn.row_factory = sqlite3.Row
    
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL DEFAULT 1,
                name TEXT NOT NULL,
                parent_id INTEGER NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(tenant_id, parent_id, name)
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL DEFAULT 1,
                filename TEXT NOT NULL,
                display_name TEXT NOT NULL,
                storage_key TEXT NOT NULL,
                mime_type TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                hash_sha256 TEXT,
                status TEXT NOT NULL DEFAULT 'ready',
                created_by INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_category_map (
                document_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY(document_id, category_id)
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                error_message TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        kb_name = f"e2e_test_kb_{get_timestamp()}"
        
        cur = conn.execute(
            "INSERT INTO knowledge_bases (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (kb_name, "E2E 测试知识库", now, now),
        )
        kb_id = cur.lastrowid
        
        conn.commit()
        
        print(f"{Colors.GREEN}[RAG] 创建知识库: {kb_name} (ID: {kb_id}){Colors.ENDC}")
        
        raw_dir = DOCS_ROOT / "raw" / f"kb_{kb_id}"
        raw_dir.mkdir(parents=True, exist_ok=True)
        
        doc_ids = []
        for doc in TEST_DOCUMENTS:
            doc_title = doc["title"]
            doc_content = doc["content"]
            
            file_name = f"{doc_title.replace(' ', '_')}.txt"
            file_path = raw_dir / file_name
            file_path.write_text(doc_content, encoding="utf-8")
            
            storage_key = f"raw/kb_{kb_id}/{file_name}"
            
            cur = conn.execute(
                "INSERT INTO documents (filename, display_name, storage_key, mime_type, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file_name, doc_title, storage_key, "text/plain", "ready", now, now),
            )
            doc_id = cur.lastrowid
            doc_ids.append(doc_id)
            
            print(f"{Colors.GREEN}[RAG] 创建文档: {doc_title} (ID: {doc_id}){Colors.ENDC}")
        
        conn.commit()
        
        expected = {
            "kb_id": kb_id,
            "kb_name": kb_name,
            "doc_count": len(doc_ids),
            "doc_ids": doc_ids,
        }
        
        return kb_id, expected
        
    finally:
        conn.close()


async def ingest_test_documents(kb_id: int, doc_ids: List[int]):
    """导入测试文档到向量数据库"""
    try:
        if str(RAG_DIR) not in sys.path:
            sys.path.insert(0, str(RAG_DIR))
        from rag.service.ingestion.ingestion_service import IngestionService
        
        ingestion = IngestionService()
        
        for doc_id in doc_ids:
            try:
                result = ingestion.ingest_document(doc_id)
                print(f"{Colors.GREEN}[RAG] 导入文档 {doc_id}: {result.get('status', 'unknown')}{Colors.ENDC}")
            except Exception as e:
                print(f"{Colors.YELLOW}[RAG] 导入文档 {doc_id} 警告: {e}{Colors.ENDC}")
                
    except ImportError as e:
        print(f"{Colors.YELLOW}[RAG] 导入模块不可用，跳过向量化: {e}{Colors.ENDC}")


async def drop_test_knowledge_base(kb_id: Optional[int]):
    """清理测试知识库"""
    if not kb_id:
        return
    
    import sqlite3
    
    try:
        conn = sqlite3.connect(RAG_META_DB)
        conn.row_factory = sqlite3.Row
        
        try:
            conn.execute("DELETE FROM documents WHERE storage_key LIKE ?", (f"raw/kb_{kb_id}/%",))
            conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
            conn.commit()
            print(f"{Colors.GREEN}[RAG] 清理知识库: {kb_id}{Colors.ENDC}")
        finally:
            conn.close()
            
        raw_dir = DOCS_ROOT / "raw" / f"kb_{kb_id}"
        if raw_dir.exists():
            import shutil
            shutil.rmtree(raw_dir)
            print(f"{Colors.GREEN}[RAG] 清理文档目录: {raw_dir}{Colors.ENDC}")
            
    except Exception as e:
        print(f"{Colors.YELLOW}[RAG] 清理警告: {e}{Colors.ENDC}")


def build_prompt(kb_id: int, reveal_docs: bool = False) -> str:
    base_prompt = (
        f"请只使用 rag_search 工具完成知识检索任务，禁止猜测数据，禁止写文件。"
        f"rag_search 工具参数：query(查询文本), kb_id(知识库ID，填写 {kb_id}), top_k(返回条数)。"
    )
    
    if reveal_docs:
        return (
            base_prompt +
            f"知识库中包含以下文档：产品使用手册、销售报告2024、员工培训手册。"
            f"请回答以下问题："
            f"1）2024年销售总额是多少？"
            f"2）同比增长率是多少？"
            f"3）销售额最高的产品是什么？"
            f"如果你觉得任务是多阶段的，可以先建立 TODO 再继续。"
            f"最后用中文输出简短结论。"
        )
    else:
        return (
            base_prompt +
            f"请先探索知识库中有哪些内容，然后回答以下问题："
            f"1）公司的标准工作时间是什么？"
            f"2）入职满一年后有多少天年假？"
            f"3）2024年销售总额是多少？"
            f"如果你觉得任务是多阶段的，可以先建立 TODO 再继续。"
            f"最后用中文输出简短结论。"
        )


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


async def run_rag_search_test(api: APIClient, output_file: str, kb_id: int, expected: dict, reveal_docs: bool = False) -> Dict:
    output_lines: List[str] = []
    errors: List[str] = []
    prompt = build_prompt(kb_id, reveal_docs=reveal_docs)

    output_lines.append(f"# RAG Search Tool E2E - {get_timestamp()}")
    output_lines.append("")

    print(f"\n{Colors.HEADER}{'=' * 72}{Colors.ENDC}")
    print(f"{Colors.HEADER}  RAG Search Tool E2E Test{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * 72}{Colors.ENDC}\n")

    print(f"{Colors.CYAN}[Step 1] Creating session...{Colors.ENDC}")
    session_result = await api.create_session("RAG Search Tool E2E")
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
    output_lines.append(f"- knowledge_base_id: {kb_id}")
    output_lines.append("")

    output_lines.append("## Expected Knowledge Base")
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
                    tool_args = metadata.get("tool_args", {})
                    result.tool_calls.append(tool_name)
                    result.tool_args_list.append({"tool_name": tool_name, "args": tool_args})
                    print(f"{Colors.MAGENTA}[tool_call] {tool_name} {json.dumps(tool_args, ensure_ascii=False)}{Colors.ENDC}")
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

    rag_search_calls = [tool for tool in result.tool_calls if tool == "rag_search"]
    if not rag_search_calls:
        errors.append(f"rag_search was not observed in tool calls: {result.tool_calls}")
    elif len(rag_search_calls) < 1:
        errors.append(f"Expected at least one rag_search call, got: {len(rag_search_calls)}")

    if "write_file" in result.tool_calls:
        errors.append(f"write_file should not be used in this RAG test: {result.tool_calls}")

    response_text = result.response_text
    
    if reveal_docs:
        required_texts = [
            EXPECTED_ANSWERS["sales_total"],
            EXPECTED_ANSWERS["growth_rate"],
            EXPECTED_ANSWERS["top_product"],
        ]
    else:
        required_texts = [
            EXPECTED_ANSWERS["work_hours"],
            EXPECTED_ANSWERS["annual_leave"],
            EXPECTED_ANSWERS["sales_total"],
        ]
    
    for expected_text in required_texts:
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
    parser = argparse.ArgumentParser(description="RAG Search Tool E2E Test")
    parser.add_argument("--no-server", action="store_true", help="Do not start server automatically")
    parser.add_argument("--user-id", type=int, default=1, help="User ID for API requests")
    parser.add_argument("--reveal", action="store_true", help="Reveal document list to agent (default: agent explores autonomously)")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip document ingestion (use existing KB)")
    args = parser.parse_args()

    backend_process = None
    started_backend = False
    kb_id = None

    try:
        settings = load_json_file(SETTINGS_PATH)
        missing_agents = ensure_rag_search_enabled(settings)
        if missing_agents:
            print(f"{Colors.RED}setting.json 未放通 rag_search: {missing_agents}{Colors.ENDC}")
            return 1
        print(f"{Colors.GREEN}setting.json 已放通 rag_search{Colors.ENDC}")

        print(f"{Colors.CYAN}Preparing test knowledge base...{Colors.ENDC}")
        kb_id, expected = await create_test_knowledge_base()
        print(f"{Colors.GREEN}Knowledge base ready: ID={kb_id}{Colors.ENDC}")

        if not args.skip_ingest:
            print(f"{Colors.CYAN}Ingesting test documents...{Colors.ENDC}")
            await ingest_test_documents(kb_id, expected["doc_ids"])

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
        output_file = logs_dir / f"rag_search_e2e_{get_timestamp()}.md"

        result = await run_rag_search_test(api, str(output_file), kb_id, expected, reveal_docs=args.reveal)
        return 0 if result.get("success") else 1

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted{Colors.ENDC}")
        return 130
    finally:
        if started_backend and backend_process is not None:
            stop_backend(backend_process)

        try:
            await drop_test_knowledge_base(kb_id)
        except Exception as e:
            print(f"{Colors.YELLOW}Cleanup warning: {e}{Colors.ENDC}")

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
