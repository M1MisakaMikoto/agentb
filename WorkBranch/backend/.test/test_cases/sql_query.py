#!/usr/bin/env python3
"""
SQL Query Test

测试 SQL 查询工具功能
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Tuple

from .base import (
    APIClient,
    TestResult,
    Colors,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


async def create_test_database(connection_settings: dict, test_rows: List[dict]) -> Tuple[str, Dict]:
    try:
        import aiomysql
    except ImportError as e:
        raise RuntimeError(f"Missing aiomysql dependency: {e}") from e

    db_name = f"agentb_sql_e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}".lower()

    async with aiomysql.connect(
        host=connection_settings.get("host", "localhost"),
        port=connection_settings.get("port", 3306),
        user=connection_settings.get("user", "root"),
        password=connection_settings.get("password", ""),
        charset="utf8mb4",
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{db_name}` DEFAULT CHARACTER SET utf8mb4")
            await conn.commit()

    async with aiomysql.connect(
        host=connection_settings.get("host", "localhost"),
        port=connection_settings.get("port", 3306),
        user=connection_settings.get("user", "root"),
        password=connection_settings.get("password", ""),
        db=db_name,
        charset="utf8mb4",
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE orders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_name VARCHAR(100),
                    category VARCHAR(50),
                    amount DECIMAL(10, 2),
                    status VARCHAR(20),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            for row in test_rows:
                await cur.execute(
                    "INSERT INTO orders (customer_name, category, amount, status) VALUES (%s, %s, %s, %s)",
                    (row["customer_name"], row["category"], row["amount"], row["status"])
                )
            
            await conn.commit()

    expected = {
        "db_name": db_name,
        "table": "orders",
        "row_count": len(test_rows),
        "total_amount": sum(float(r["amount"]) for r in test_rows),
        "categories": list(set(r["category"] for r in test_rows)),
    }

    return db_name, expected


async def cleanup_test_database(connection_settings: dict, db_name: str):
    try:
        import aiomysql
    except ImportError:
        return

    async with aiomysql.connect(
        host=connection_settings.get("host", "localhost"),
        port=connection_settings.get("port", 3306),
        user=connection_settings.get("user", "root"),
        password=connection_settings.get("password", ""),
        charset="utf8mb4",
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
            await conn.commit()


async def run_sql_query_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("sql_query", scenario_config)
    
    print_test_header(scenario_config.get("description", "SQL Query Test"))
    
    connection_settings = {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "",
    }
    
    test_rows = scenario_config.get("test_rows", [
        {"customer_name": "Alice", "category": "electronics", "amount": 120, "status": "paid"},
        {"customer_name": "Bob", "category": "books", "amount": 35, "status": "paid"},
        {"customer_name": "Cara", "category": "electronics", "amount": 80, "status": "pending"},
        {"customer_name": "Dan", "category": "books", "amount": 20, "status": "paid"},
        {"customer_name": "Eve", "category": "grocery", "amount": 15, "status": "paid"},
        {"customer_name": "Frank", "category": "grocery", "amount": 40, "status": "cancelled"},
    ])
    
    db_name = None
    
    try:
        print_step(1, "Creating test database...", Colors.CYAN)
        try:
            db_name, expected = await create_test_database(connection_settings, test_rows)
            print_success(f"Database created: {db_name}")
            print_dim(f"Table: {expected['table']}, Rows: {expected['row_count']}")
        except Exception as e:
            print_error(f"Failed to create test database: {e}")
            result.errors.append(f"create_database: {e}")
            return result
        
        print_step(2, "Creating session...", Colors.CYAN)
        session_result = await api.create_session(title="SQL Query Test")
        if not session_result.get("success", True):
            print_error(f"Failed to create session: {session_result.get('message')}")
            result.errors.append(f"create_session: {session_result.get('message')}")
            return result
        
        session_id = session_result.get("data", {}).get("id")
        result.session_id = session_id
        print_success(f"Session created: {session_id}")
        
        print_step(3, "Creating conversation with SQL query...", Colors.CYAN)
        question = f"请查询数据库 {db_name} 中 orders 表的所有记录，并统计各类别的销售总额"
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
        
        sql_tools = ["sql_query", "query_database", "execute_sql"]
        found_sql_tool = any(tool in result.tool_calls for tool in sql_tools)
        if found_sql_tool:
            print_success("SQL query tool was called")
        else:
            print_dim("SQL query tool may not have been called (check tool availability)")
        
        if result.response_text:
            print_success(f"Response length: {len(result.response_text)} chars")
        else:
            print_error("No response text found")
        
        print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
        print(f"{Colors.GREEN}  SQL Query Test Completed{Colors.ENDC}")
        print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
        
        return result
        
    finally:
        if db_name:
            print_step(8, "Cleaning up test database...", Colors.CYAN)
            await cleanup_test_database(connection_settings, db_name)
            print_success("Cleanup completed")


async def run_sql_agent_bridge_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("sql_agent_bridge", scenario_config)
    
    print_test_header(scenario_config.get("description", "SQL Agent Bridge Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="SQL Agent Bridge Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "Creating conversation with SQL agent bridge query...", Colors.CYAN)
    prompt = scenario_config.get("prompt", "我需要统计智慧管养系统中桥梁基础数据的总数，需要调用工具查数据库")
    conv_result = await api.create_conversation(session_id, prompt)
    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")
    
    print_step(3, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
    
    print_step(4, "Streaming response...", Colors.CYAN)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    
    print_step(5, "Waiting for conversation to complete...", Colors.CYAN)
    final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=120.0)
    result.response_text = extract_response_text(final_result)
    
    print_step(6, "Validating results...", Colors.CYAN)
    
    if result.response_text:
        print_success(f"Response length: {len(result.response_text)} chars")
    else:
        print_error("No response text found")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  SQL Agent Bridge Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
