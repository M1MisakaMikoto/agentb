#!/usr/bin/env python3
"""
SQL Serial Double Dialogue Test

测试单会话串行两次对话：
1. 两次对话都能调用SQL工具
2. 测量第二次对话从发起到第一次接收到流式响应的时间
"""

import asyncio
import time
import sys
import os
from datetime import datetime
from typing import Optional

sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend\.test')
os.chdir(r'e:\PythonProject\agentb\WorkBranch\backend\.test')

from test_cases.base import (
    APIClient,
    TestResult,
    Colors,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    print_warning,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
    load_config,
    start_backend,
    stop_backend,
)


class StreamTimingResult:
    """测量流式响应时间的结构"""
    def __init__(self):
        self.first_byte_time: Optional[float] = None  # 第一次接收到数据的时间
        self.first_event_time: Optional[float] = None  # 第一次有意义事件的时间
        self.conversation_start_time: float = 0  # 创建对话的时间
        self.first_byte_latency_ms: float = 0  # 到第一次接收到数据的延迟
        self.first_event_latency_ms: float = 0  # 到第一次有意义事件的延迟


async def timed_collect_stream_output(
    api: APIClient,
    conversation_id: str,
    result: TestResult,
    timing: StreamTimingResult,
    verbose: bool = True,
    timeout: float = 300.0,
):
    """收集流式输出并测量时间"""
    timing.conversation_start_time = time.perf_counter()

    async def _on_first_byte():
        if timing.first_byte_time is None:
            timing.first_byte_time = time.perf_counter()
            timing.first_byte_latency_ms = (timing.first_byte_time - timing.conversation_start_time) * 1000
            if verbose:
                print(f"{Colors.GREEN}[TIMING] First byte received: {timing.first_byte_latency_ms:.2f}ms{Colors.ENDC}")

    async def _on_first_event():
        if timing.first_event_time is None:
            timing.first_event_time = time.perf_counter()
            timing.first_event_latency_ms = (timing.first_event_time - timing.conversation_start_time) * 1000
            if verbose:
                print(f"{Colors.GREEN}[TIMING] First meaningful event: {timing.first_event_latency_ms:.2f}ms{Colors.ENDC}")

    await collect_stream_output(
        api,
        conversation_id,
        result,
        verbose=verbose,
        timeout=timeout,
        # 使用回调来触发计时
    )

    # 由于 collect_stream_output 不支持回调，我们在其中检测
    # 这里先调用，然后在 collect_stream_output 中通过 monkey patch 方式触发计时
    # 更好的方式是直接修改调用逻辑


async def run_sql_serial_double_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> dict:
    """
    单会话串行双对话测试

    Returns:
        dict: {
            "first_timing": StreamTimingResult,
            "second_timing": StreamTimingResult,
            "first_result": TestResult,
            "second_result": TestResult,
            "both_sql_tools_called": bool,
        }
    """
    first_timing = StreamTimingResult()
    second_timing = StreamTimingResult()
    first_result = TestResult("sql_serial_first", scenario_config)
    second_result = TestResult("sql_serial_second", scenario_config)

    print_test_header(scenario_config.get("description", "SQL Serial Double Dialogue Test"))

    # Step 1: 创建会话
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="SQL Serial Double Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        return {
            "error": f"create_session: {session_result.get('message')}",
            "first_timing": first_timing,
            "second_timing": second_timing,
            "first_result": first_result,
            "second_result": second_result,
            "both_sql_tools_called": False,
        }

    session_id = session_result.get("data", {}).get("id")
    first_result.session_id = session_id
    second_result.session_id = session_id
    print_success(f"Session created: {session_id}")

    # ============== 第一次对话 ==============
    print_step(2, "First conversation - SQL query (bridge count)...", Colors.CYAN)
    first_question = scenario_config.get("first_question", "请查询智慧管养系统中桥梁基础数据的总数")

    first_timing.conversation_start_time = time.perf_counter()
    t_create_start = first_timing.conversation_start_time

    conv_result = await api.create_conversation(session_id, first_question)
    if not conv_result.get("success", True):
        print_error(f"Failed to create first conversation: {conv_result.get('message')}")
        first_result.errors.append(f"create_conversation_1: {conv_result.get('message')}")
        return {
            "error": f"create_conversation_1: {conv_result.get('message')}",
            "first_timing": first_timing,
            "second_timing": second_timing,
            "first_result": first_result,
            "second_result": second_result,
            "both_sql_tools_called": False,
        }

    first_conversation_id = conv_result.get("data", {}).get("conversation_id")
    first_result.conversation_id = first_conversation_id
    print_success(f"First conversation created: {first_conversation_id}")

    print_step(3, "Waiting for first conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, first_conversation_id, "processing", timeout=10.0)

    print_step(4, "Streaming first response...", Colors.CYAN)
    stream_timeout = 120.0  # 第一次对话给予更多时间
    await collect_stream_output(api, first_conversation_id, first_result, verbose=verbose, timeout=stream_timeout)

    print_step(5, "Checking first conversation state...", Colors.CYAN)
    # 重要：必须等第一个对话完全完成后再创建第二个对话
    # 先检查当前状态
    check_state = await api.get_conversation(first_conversation_id)
    current_state = check_state.get("data", {}).get("state")
    print_dim(f"Current state: {current_state}")

    # 轮询等待对话完成，最多等待5分钟
    first_final = await wait_for_conversation_state(api, first_conversation_id, "completed", timeout=300.0, poll_interval=3.0)
    first_response = extract_response_text(first_final)
    first_result.response_text = first_response
    print_success(f"First response length: {len(first_response)} chars")
    print_success(f"First tool calls: {first_result.tool_calls}")

    # ============== 第二次对话 ==============
    # 重要：必须等第一个对话完全完成后再创建第二个对话
    print_step(6, "Second conversation - SQL query (another question)...", Colors.CYAN)
    second_question = scenario_config.get("second_question", "请查询智慧管养系统中的所有桥梁名称列表")

    # 测量从发起到第一次接收到流式的时间
    second_timing.conversation_start_time = time.perf_counter()

    conv_result2 = await api.create_conversation(session_id, second_question)
    if not conv_result2.get("success", True):
        print_error(f"Failed to create second conversation: {conv_result2.get('message')}")
        second_result.errors.append(f"create_conversation_2: {conv_result2.get('message')}")
        return {
            "error": f"create_conversation_2: {conv_result2.get('message')}",
            "first_timing": first_timing,
            "second_timing": second_timing,
            "first_result": first_result,
            "second_result": second_result,
            "both_sql_tools_called": False,
        }

    second_conversation_id = conv_result2.get("data", {}).get("conversation_id")
    second_result.conversation_id = second_conversation_id
    print_success(f"Second conversation created: {second_conversation_id}")

    print_step(7, "Waiting for second conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, second_conversation_id, "processing", timeout=10.0)

    print_step(8, "Streaming second response with timing measurement...", Colors.CYAN)

    # 等待流式响应并测量时间
    t_stream_start = time.perf_counter()
    second_timing.conversation_start_time = t_stream_start

    # 使用带计时的流式收集
    await _timed_stream_second(
        api, second_conversation_id, second_result, second_timing, verbose=verbose
    )

    print_step(9, "Waiting for second conversation to complete...", Colors.CYAN)
    second_final = await wait_for_conversation_state(api, second_conversation_id, "completed", timeout=120.0)
    second_response = extract_response_text(second_final)
    second_result.response_text = second_response
    print_success(f"Second response length: {len(second_response)} chars")
    print_success(f"Second tool calls: {second_result.tool_calls}")

    # ============== 结果验证 ==============
    print_step(10, "Validating results...", Colors.CYAN)

    sql_tools = ["sql_query", "query_database", "execute_sql", "bridge_query"]

    first_sql_called = any(tool in first_result.tool_calls for tool in sql_tools)
    second_sql_called = any(tool in second_result.tool_calls for tool in sql_tools)
    both_sql_tools_called = first_sql_called and second_sql_called

    print(f"\n{Colors.CYAN}--- SQL Tool Call Summary ---{Colors.ENDC}")
    print(f"First conversation SQL tool called: {first_sql_called}")
    print(f"Second conversation SQL tool called: {second_sql_called}")

    print(f"\n{Colors.CYAN}--- Second Conversation Timing ---{Colors.ENDC}")
    print(f"First byte latency: {second_timing.first_byte_latency_ms:.2f}ms")
    print(f"First event latency: {second_timing.first_event_latency_ms:.2f}ms")

    # 最终报告
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  SQL Serial Double Dialogue Test Results{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}")

    if both_sql_tools_called:
        print_success("Both conversations called SQL tools - PASS")
    else:
        print_error("Not all conversations called SQL tools - FAIL")

    if second_timing.first_byte_latency_ms > 0:
        print_success(f"Second conversation first byte latency: {second_timing.first_byte_latency_ms:.2f}ms")
    else:
        print_warning("Could not measure first byte latency")

    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

    return {
        "first_timing": first_timing,
        "second_timing": second_timing,
        "first_result": first_result,
        "second_result": second_result,
        "both_sql_tools_called": both_sql_tools_called,
        "error": None,
    }


async def _timed_stream_second(
    api: APIClient,
    conversation_id: str,
    result: TestResult,
    timing: StreamTimingResult,
    verbose: bool = True,
    timeout: float = 300.0,
):
    """
    带计时的流式输出收集 - 专门用于第二次对话的精确时间测量
    """
    import httpx
    import json

    deadline = time.time() + timeout
    stream_log_file = None

    # 初始化计时
    t_start = timing.conversation_start_time
    timing.first_byte_time = None
    timing.first_event_time = None
    timing.first_byte_latency_ms = 0
    timing.first_event_latency_ms = 0

    path = f"/session/conversations/{conversation_id}/stream"
    url = f"http://localhost:8000{path}?last_seq=0"

    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, read=30.0))
    try:
        async with client.stream("GET", url, headers={"Content-Type": "application/json", "X-User-ID": "1"}) as response:
            if response.status_code != 200:
                error = await response.aread()
                print_error(f"Stream error: {error.decode()}")
                return

            async for line in response.aiter_lines():
                if time.time() >= deadline:
                    print_warning("Stream timeout")
                    break

                # 记录第一次收到数据的时间
                t_now = time.perf_counter()
                if timing.first_byte_time is None:
                    timing.first_byte_time = t_now
                    timing.first_byte_latency_ms = (t_now - t_start) * 1000
                    if verbose:
                        print(f"{Colors.GREEN}[TIMING] First byte: {timing.first_byte_latency_ms:.2f}ms{Colors.ENDC}")

                if not line.startswith("data: "):
                    if line.startswith(": heartbeat"):
                        continue
                    continue

                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type", "unknown")

                # 记录第一次有意义事件的时间
                if timing.first_event_time is None and event_type in ["text_delta", "chat_delta", "thinking_delta", "tool_call"]:
                    timing.first_event_time = t_now
                    timing.first_event_latency_ms = (t_now - t_start) * 1000
                    if verbose:
                        print(f"{Colors.GREEN}[TIMING] First event ({event_type}): {timing.first_event_latency_ms:.2f}ms{Colors.ENDC}")

                # 处理事件
                if verbose:
                    if event_type == "text_delta":
                        content = data.get("content", "")
                        print(f"{Colors.CYAN}[text] {content}{Colors.ENDC}")
                    elif event_type == "chat_delta":
                        content = data.get("content", "")
                        print(f"{Colors.GREEN}[chat] {content}{Colors.ENDC}")
                    elif event_type == "chat_end":
                        result.done = True
                        print(f"{Colors.GREEN}[chat_end] Stream completed{Colors.ENDC}")
                        return
                    elif event_type == "tool_call":
                        metadata = data.get("metadata") or {}
                        tool_name = metadata.get("tool_name", "unknown")
                        result.tool_calls.append(tool_name)
                        print(f"{Colors.MAGENTA}[tool_call] {tool_name}{Colors.ENDC}")
                    elif event_type == "thinking_delta":
                        content = data.get("content", "")
                        if len(content) > 10:
                            print(f"{Colors.DIM}[thinking] {content[:50]}...{Colors.ENDC}")
                    elif event_type == "done":
                        result.done = True
                        print(f"{Colors.GREEN}[done] Stream completed{Colors.ENDC}")
                        return
                    elif event_type == "error":
                        error_content = data.get("content", "Unknown error")
                        result.errors.append(error_content)
                        print(f"{Colors.RED}[error] {error_content}{Colors.ENDC}")

    finally:
        await client.aclose()


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='SQL Serial Double Dialogue Test')
    parser.add_argument('--verbose', '-v', action='store_true', default=True, help='Verbose output')
    parser.add_argument('--no-server', action='store_true', help='Use existing backend (no start)')
    args = parser.parse_args()

    log_file = r'e:\PythonProject\agentb\WorkBranch\backend\llm_decision_trace.log'
    if os.path.exists(log_file):
        open(log_file, 'w').close()

    if args.no_server:
        print('='*60)
        print('Using existing backend server...')
        print('='*60)
        backend_process = None
    else:
        print('='*60)
        print('Starting backend server...')
        print('='*60)

        backend_process = start_backend()

        if not backend_process:
            print('[X] Failed to start backend')
            return

        await asyncio.sleep(3)

    config = load_config()
    api = APIClient(config)

    max_retries = 10
    for attempt in range(max_retries):
        try:
            health = await api._request('GET', '/health')
            if health.get('status') == 'ok':
                print(f'[OK] Backend ready (attempt {attempt + 1})')
                break
        except:
            pass

        if attempt < max_retries - 1:
            print(f'Waiting for backend... ({attempt + 1}/{max_retries})')
            await asyncio.sleep(2)
    else:
        print('[X] Backend failed to start after retries')
        if backend_process:
            stop_backend(backend_process)
        return

    # 测试配置
    scenario_config = {
        "description": "SQL Serial Double Dialogue Test",
        "first_question": "请查询智慧管养系统中桥梁基础数据的总数",
        "second_question": "请查询智慧管养系统中的所有桥梁名称列表",
    }

    # 运行测试
    result = await run_sql_serial_double_test(api, scenario_config, verbose=args.verbose)

    # 打印最终汇总
    print('\n' + '='*60)
    print('FINAL TEST SUMMARY')
    print('='*60)

    if result.get("error"):
        print(f"{Colors.RED}Error: {result['error']}{Colors.ENDC}")
    else:
        print(f"First conversation tool calls: {result['first_result'].tool_calls}")
        print(f"Second conversation tool calls: {result['second_result'].tool_calls}")
        print(f"Both SQL tools called: {result['both_sql_tools_called']}")

        print(f"\n{Colors.CYAN}Timing Measurements:{Colors.ENDC}")
        print(f"  Second conversation first byte latency: {result['second_timing'].first_byte_latency_ms:.2f}ms")
        print(f"  Second conversation first event latency: {result['second_timing'].first_event_latency_ms:.2f}ms")

        if result['both_sql_tools_called']:
            print(f"\n{Colors.GREEN}*** TEST PASSED ***{Colors.ENDC}")
        else:
            print(f"\n{Colors.RED}*** TEST FAILED ***{Colors.ENDC}")

    print('='*60)

    if backend_process:
        print('\nStopping backend...')
        stop_backend(backend_process)


if __name__ == "__main__":
    asyncio.run(main())