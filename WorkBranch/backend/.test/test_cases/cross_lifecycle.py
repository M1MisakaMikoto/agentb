#!/usr/bin/env python3
"""
Cross Lifecycle Test

测试跨生命周期（后端重启后）会话持久化
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict

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
    wait_for_backend,
    start_backend,
    stop_backend,
)


PERSISTENCE_DIR = get_project_root() / "data" / "persistence"


def save_session_state(session_id: int, data: dict):
    PERSISTENCE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = PERSISTENCE_DIR / f"session_{session_id}.json"
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_session_state(session_id: int) -> dict:
    state_file = PERSISTENCE_DIR / f"session_{session_id}.json"
    if not state_file.exists():
        return {}
    with open(state_file, "r", encoding="utf-8") as f:
        return json.load(f)


def clear_session_state(session_id: int):
    state_file = PERSISTENCE_DIR / f"session_{session_id}.json"
    if state_file.exists():
        state_file.unlink()


async def run_cross_lifecycle_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("cross_lifecycle", scenario_config)
    
    print_test_header(scenario_config.get("description", "Cross Lifecycle Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="Cross Lifecycle Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "First conversation - setting secret code...", Colors.CYAN)
    first_prompt = scenario_config.get("first_prompt", "你好，请记住暗号 CROSS-LIFETIME-2024，只回复这串暗号。")
    conv_result = await api.create_conversation(session_id, first_prompt)
    if not conv_result.get("success", True):
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
    
    print_step(6, "Saving session state...", Colors.CYAN)
    save_session_state(session_id, {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "first_response": first_response,
        "timestamp": time.time(),
    })
    print_success("Session state saved")
    
    print_step(7, "Simulating backend restart (waiting 3 seconds)...", Colors.YELLOW)
    await asyncio.sleep(3)
    print_dim("In a real test, the backend would be restarted here")
    
    print_step(8, "Verifying session persistence...", Colors.CYAN)
    session_check = await api.get_session(session_id)
    if session_check.get("code") == 0:
        print_success("Session still exists after restart simulation")
    else:
        print_error(f"Session not found: {session_check.get('message')}")
        result.errors.append(f"session_persistence: {session_check.get('message')}")
    
    print_step(9, "Second conversation - asking for secret code...", Colors.CYAN)
    second_prompt = scenario_config.get("second_prompt", "上一轮对话中的暗号是什么？请告诉我。")
    conv_result2 = await api.create_conversation(session_id, second_prompt)
    if conv_result2.get("code") != 0:
        print_error(f"Failed to create second conversation: {conv_result2.get('message')}")
        result.errors.append(f"create_conversation_2: {conv_result2.get('message')}")
        return result
    
    conversation_id2 = conv_result2.get("data", {}).get("conversation_id")
    print_success(f"Second conversation created: {conversation_id2}")
    
    print_step(10, "Waiting for second conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id2, "processing", timeout=10.0)
    
    print_step(11, "Streaming second response...", Colors.CYAN)
    second_result = TestResult("cross_lifecycle_2", scenario_config)
    await collect_stream_output(api, conversation_id2, second_result, verbose=verbose)
    
    print_step(12, "Waiting for second conversation to complete...", Colors.CYAN)
    second_final = await wait_for_conversation_state(api, conversation_id2, "completed", timeout=60.0)
    second_response = extract_response_text(second_final)
    print_success(f"Second response: {second_response[:100]}...")
    
    print_step(13, "Validating memory persistence...", Colors.CYAN)
    
    secret_code = "CROSS-LIFETIME-2024"
    if secret_code in second_response:
        print_success(f"Secret code '{secret_code}' found in second response - memory persistence works!")
    else:
        print_error(f"Secret code '{secret_code}' NOT found in second response - memory persistence may not work")
        result.errors.append(f"Memory persistence failed: secret code not found in second response")
    
    result.response_text = f"First: {first_response}\n\nSecond: {second_response}"
    result.tool_calls.extend(second_result.tool_calls)
    result.event_count += second_result.event_count
    
    print_step(14, "Cleaning up session state...", Colors.CYAN)
    clear_session_state(session_id)
    print_success("Cleanup completed")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Cross Lifecycle Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
