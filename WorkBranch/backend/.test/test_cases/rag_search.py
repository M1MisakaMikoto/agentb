#!/usr/bin/env python3
"""
RAG Search Test

测试 RAG 搜索工具功能
"""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

from .base import (
    APIClient,
    TestResult,
    Colors,
    get_project_root,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


RAG_META_DB = get_project_root() / "data" / "rag" / "meta.db"
DOCS_ROOT = get_project_root() / "data" / "rag" / "docs"


async def create_test_knowledge_base(scenario_config: dict) -> Tuple[int, Dict]:
    RAG_META_DB.parent.mkdir(parents=True, exist_ok=True)
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(RAG_META_DB)
    conn.row_factory = sqlite3.Row
    
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kb_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                category TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id)
            )
        """)
        
        now = datetime.now().isoformat()
        kb_name = f"test_kb_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        cursor.execute(
            "INSERT INTO knowledge_bases (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (kb_name, "Test knowledge base for E2E testing", now, now)
        )
        kb_id = cursor.lastrowid
        
        test_documents = scenario_config.get("test_documents", [
            {"title": "产品使用手册", "category": "技术文档", "content": "这是产品使用手册的内容..."},
            {"title": "销售报告 2024", "category": "业务报告", "content": "这是2024年销售报告的内容..."},
            {"title": "员工培训手册", "category": "人力资源", "content": "这是员工培训手册的内容..."},
        ])
        
        expected = {"kb_id": kb_id, "documents": []}
        
        for doc in test_documents:
            cursor.execute(
                "INSERT INTO documents (kb_id, title, content, category, created_at) VALUES (?, ?, ?, ?, ?)",
                (kb_id, doc["title"], doc.get("content", ""), doc.get("category", ""), now)
            )
            expected["documents"].append({
                "id": cursor.lastrowid,
                "title": doc["title"],
                "category": doc.get("category", "")
            })
        
        conn.commit()
        return kb_id, expected
        
    finally:
        conn.close()


async def cleanup_test_knowledge_base(kb_id: int):
    if not RAG_META_DB.exists():
        return
    
    conn = sqlite3.connect(RAG_META_DB)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM documents WHERE kb_id = ?", (kb_id,))
        cursor.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
        conn.commit()
    finally:
        conn.close()


async def run_rag_search_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("rag_search", scenario_config)
    
    print_test_header(scenario_config.get("description", "RAG Search Test"))
    
    print_step(1, "Creating test knowledge base...", Colors.CYAN)
    try:
        kb_id, expected = await create_test_knowledge_base(scenario_config)
        print_success(f"Knowledge base created: {kb_id}")
        print_dim(f"Documents: {len(expected['documents'])}")
    except Exception as e:
        print_error(f"Failed to create knowledge base: {e}")
        result.errors.append(f"create_knowledge_base: {e}")
        return result
    
    try:
        print_step(2, "Creating session...", Colors.CYAN)
        session_result = await api.create_session(title="RAG Search Test")
        if not session_result.get("success", True):
            print_error(f"Failed to create session: {session_result.get('message')}")
            result.errors.append(f"create_session: {session_result.get('message')}")
            return result
        
        session_id = session_result.get("data", {}).get("id")
        result.session_id = session_id
        print_success(f"Session created: {session_id}")
        
        print_step(3, "Creating conversation with RAG search query...", Colors.CYAN)
        question = f"请使用 RAG 搜索工具在知识库 {kb_id} 中搜索关于产品的信息"
        conv_result = await api.create_conversation(session_id, question)
        if not conv_result.get("success", True):
            print_error(f"Failed to create conversation: {conv_result.get('message')}")
            result.errors.append(f"create_conversation: {conv_result.get('message')}")
            return result
        
        conversation_id = conv_result.get("data", {}).get("conversation_id")
        result.conversation_id = conversation_id
        print_success(f"Conversation created: {conversation_id}")
        
        print_step(4, "Waiting for conversation to be processing...", Colors.CYAN)
        await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
        
        print_step(5, "Streaming response...", Colors.CYAN)
        await collect_stream_output(api, conversation_id, result, verbose=verbose)
        
        print_step(6, "Waiting for conversation to complete...", Colors.CYAN)
        final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=120.0)
        result.response_text = extract_response_text(final_result)
        
        print_step(7, "Validating results...", Colors.CYAN)
        
        rag_tools = ["rag_search", "knowledge_search", "search_knowledge"]
        found_rag_tool = any(tool in result.tool_calls for tool in rag_tools)
        if found_rag_tool:
            print_success("RAG search tool was called")
        else:
            print_dim("RAG search tool may not have been called (check tool availability)")
        
        if result.response_text:
            print_success(f"Response length: {len(result.response_text)} chars")
        else:
            print_error("No response text found")
        
        print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
        print(f"{Colors.GREEN}  RAG Search Test Completed{Colors.ENDC}")
        print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
        
        return result
        
    finally:
        print_step(8, "Cleaning up test knowledge base...", Colors.CYAN)
        await cleanup_test_knowledge_base(kb_id)
        print_success("Cleanup completed")
