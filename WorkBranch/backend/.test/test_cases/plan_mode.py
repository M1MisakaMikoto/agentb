#!/usr/bin/env python3
"""V4 call_plan_agent E2E scenario."""

from .base import (
    APIClient,
    Colors,
    TestResult,
    collect_stream_output,
    extract_response_text,
    print_error,
    print_step,
    print_success,
    print_test_header,
    wait_for_conversation_state,
)


async def run_plan_mode_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("plan_mode", scenario_config)
    print_test_header(scenario_config.get("description", "V4 plan agent"))

    session_result = await api.create_session(title="V4 plan agent")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    result.workspace_id = workspace_id
    question = scenario_config.get(
        "question",
        "请先调用 call_plan_agent，为实现用户登录功能生成执行计划；"
        "计划写入后直接输出最终总结，不使用旧 PLAN 模式。",
    )
    conversation_result = await api.create_conversation(session_id, question)
    if not conversation_result.get("success", True):
        result.errors.append(
            f"create_conversation: {conversation_result.get('message')}"
        )
        return result

    data = conversation_result.get("data", {})
    conversation_id = data.get("conversation_id")
    result.conversation_id = conversation_id
    result.workspace_id = data.get("workspace_id") or workspace_id
    print_step(1, "等待计划子代理完成...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=300.0)
    completed = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=300.0
    )
    result.response_text = extract_response_text(completed)

    plan_result = await api.get_plan(result.workspace_id)
    plan_data = plan_result.get("data", {})
    if not plan_data.get("exists") or not str(plan_data.get("content") or "").strip():
        result.errors.append("call_plan_agent_did_not_create_plan")
    if not result.response_text.strip():
        result.errors.append("empty_response_after_plan")
    if result.detected_modes:
        result.errors.append(f"retired_execution_mode_emitted:{result.detected_modes}")

    if result.errors:
        for error in result.errors:
            print_error(error)
    else:
        print_success("call_plan_agent 写入计划并完成总结")
    return result
