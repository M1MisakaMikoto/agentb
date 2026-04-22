#!/usr/bin/env python3
"""
SEARCH Mode Test

测试 SEARCH 模式 - 网络搜索与文件查看
"""

import asyncio

from .base import (
    APIClient,
    TestResult,
    Colors,
    print_test_header,
    print_step,
    print_success,
    print_error,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


async def run_search_mode_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("search_mode", scenario_config)
    
    print_test_header(scenario_config.get("description", "SEARCH Mode Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="SEARCH Mode Test")
    if session_result.get("code") != 0:
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "Creating conversation...", Colors.CYAN)
    question = scenario_config.get("question", "请使用 explore_internet 工具搜索市政设施管理规定的相关法规")
    conv_result = await api.create_conversation(session_id, question)
    if conv_result.get("code") != 0:
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
    final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=180.0)
    result.response_text = extract_response_text(final_result)
    
    print_step(6, "Validating results...", Colors.CYAN)
    
    expected_mode = scenario_config.get("expected_mode")
    if expected_mode and result.detected_mode:
        if result.detected_mode == expected_mode:
            print_success(f"Mode check passed: {result.detected_mode}")
        else:
            print_error(f"Mode mismatch: expected {expected_mode}, got {result.detected_mode}")
    
    expected_tools = scenario_config.get("expected_tools", [])
    if expected_tools:
        for tool in expected_tools:
            if tool in result.tool_calls:
                print_success(f"Expected tool found: {tool}")
            else:
                print_error(f"Expected tool not found: {tool}")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  SEARCH Mode Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
