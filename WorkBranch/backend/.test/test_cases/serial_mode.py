#!/usr/bin/env python3
"""
SERIAL Mode Test

测试 SERIAL 模式 - 同一Session串行对话约束与历史继承测试
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


async def run_serial_mode_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("serial_mode", scenario_config)
    
    print_test_header(scenario_config.get("description", "SERIAL Mode Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="SERIAL Mode Test")
    if session_result.get("code") != 0:
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "First conversation - setting secret code...", Colors.CYAN)
    first_question = scenario_config.get("question", "请记住暗号 ALPHA-9271，只回复这串暗号。")
    conv_result = await api.create_conversation(session_id, first_question)
    if conv_result.get("code") != 0:
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"First conversation created: {conversation_id}")
    
    print_step(3, "Waiting for first conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
    
    print_step(4, "Streaming first response...", Colors.CYAN)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    
    print_step(5, "Waiting for first conversation to complete...", Colors.CYAN)
    first_final = await wait_for_conversation_state(api, conversation_id, "completed", timeout=60.0)
    first_response = extract_response_text(first_final)
    print_success(f"First response: {first_response[:100]}...")
    
    print_step(6, "Second conversation - asking for secret code...", Colors.CYAN)
    second_question = "上一轮对话中的暗号是什么？请告诉我。"
    conv_result2 = await api.create_conversation(session_id, second_question)
    if conv_result2.get("code") != 0:
        print_error(f"Failed to create second conversation: {conv_result2.get('message')}")
        result.errors.append(f"create_conversation_2: {conv_result2.get('message')}")
        return result
    
    conversation_id2 = conv_result2.get("data", {}).get("conversation_id")
    print_success(f"Second conversation created: {conversation_id2}")
    
    print_step(7, "Waiting for second conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id2, "processing", timeout=10.0)
    
    print_step(8, "Streaming second response...", Colors.CYAN)
    second_result = TestResult("serial_mode_2", scenario_config)
    await collect_stream_output(api, conversation_id2, second_result, verbose=verbose)
    
    print_step(9, "Waiting for second conversation to complete...", Colors.CYAN)
    second_final = await wait_for_conversation_state(api, conversation_id2, "completed", timeout=60.0)
    second_response = extract_response_text(second_final)
    print_success(f"Second response: {second_response[:100]}...")
    
    print_step(10, "Validating memory inheritance...", Colors.CYAN)
    
    secret_code = "ALPHA-9271"
    if secret_code in second_response:
        print_success(f"Secret code '{secret_code}' found in second response - memory inheritance works!")
    else:
        print_error(f"Secret code '{secret_code}' NOT found in second response - memory inheritance may not work")
        result.errors.append(f"Memory inheritance failed: secret code not found in second response")
    
    result.response_text = f"First: {first_response}\n\nSecond: {second_response}"
    result.tool_calls.extend(second_result.tool_calls)
    result.event_count += second_result.event_count
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  SERIAL Mode Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
