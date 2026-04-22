#!/usr/bin/env python3
"""
PLAN Mode Test

测试 PLAN 模式 - 计划后等待批准再执行
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
    print_dim,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


async def run_plan_mode_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("plan_mode", scenario_config)
    
    print_test_header(scenario_config.get("description", "PLAN Mode Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="PLAN Mode Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "Creating conversation...", Colors.CYAN)
    question = scenario_config.get("question", "请把'实现一个简单的用户登录功能'当作复杂多阶段开发任务处理。")
    conv_result = await api.create_conversation(session_id, question)
    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    workspace_id = conv_result.get("data", {}).get("workspace_id")
    result.workspace_id = workspace_id
    print_success(f"Conversation created: {conversation_id}")
    print_dim(f"Workspace ID: {workspace_id}")
    
    print_step(3, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
    
    print_step(4, "Streaming response (waiting for plan)...", Colors.CYAN)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    
    print_step(5, "Waiting for conversation to complete or plan approval...", Colors.CYAN)
    final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=120.0)
    result.response_text = extract_response_text(final_result)
    
    if result.plan_status == "pending_approval":
        print_step(6, "Plan is pending approval, approving...", Colors.YELLOW)
        approval_message = scenario_config.get("approval_message", "可以")
        
        approve_result = await api.approve_plan(workspace_id, approved=True)
        if not approve_result.get("success", True):
            print_error(f"Failed to approve plan: {approve_result.get('message')}")
            result.errors.append(f"approve_plan: {approve_result.get('message')}")
        else:
            print_success("Plan approved")
            
            next_conv_id = approve_result.get("data", {}).get("conversation_id")
            if next_conv_id:
                result.conversation_id = next_conv_id
                print_dim(f"New conversation ID: {next_conv_id}")
                
                print_step(7, "Streaming execution response...", Colors.CYAN)
                await wait_for_conversation_state(api, next_conv_id, "processing", timeout=10.0)
                await collect_stream_output(api, next_conv_id, result, verbose=verbose)
                
                final_result = await wait_for_conversation_state(api, next_conv_id, "completed", timeout=180.0)
                result.response_text = extract_response_text(final_result)
    else:
        print_dim(f"Plan status: {result.plan_status}")
    
    print_step(8, "Validating results...", Colors.CYAN)
    
    expected_modes = scenario_config.get("expected_modes", [])
    if expected_modes and result.detected_modes:
        for mode in expected_modes:
            if mode in result.detected_modes:
                print_success(f"Expected mode found: {mode}")
            else:
                print_error(f"Expected mode not found: {mode}")
    
    forbidden_tools = scenario_config.get("forbidden_tools", [])
    if forbidden_tools:
        for tool in forbidden_tools:
            if tool in result.tool_calls:
                print_error(f"Forbidden tool found: {tool}")
            else:
                print_success(f"Forbidden tool not used: {tool}")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  PLAN Mode Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
