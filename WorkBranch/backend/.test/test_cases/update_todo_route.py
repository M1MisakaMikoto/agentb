#!/usr/bin/env python3
"""V4 update_todo execution-flow E2E scenario."""

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


async def run_update_todo_route_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("update_todo_route", scenario_config)
    print_test_header(scenario_config.get("description", "V4 update_todo flow"))

    session_result = await api.create_session(title="V4 update_todo flow")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    prompt = scenario_config.get(
        "prompt",
        "请先调用 list_workspace_files，再调用 update_todo 创建并完成一个待办，"
        "最后直接输出最终总结。",
    )
    conversation_result = await api.create_conversation(session_id, prompt)
    if not conversation_result.get("success", True):
        result.errors.append(
            f"create_conversation: {conversation_result.get('message')}"
        )
        return result

    conversation_id = conversation_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_step(1, "执行 V4 todo 流程...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=240.0)
    completed = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=240.0
    )
    result.response_text = extract_response_text(completed)

    for tool_name in ("list_workspace_files", "update_todo"):
        if tool_name not in result.tool_calls:
            result.errors.append(f"missing_tool_call:{tool_name}")
    if not result.response_text.strip():
        result.errors.append("empty_response_after_update_todo")
    if "chat" in result.tool_calls:
        result.errors.append("retired_chat_tool_called")

    if result.errors:
        for error in result.errors:
            print_error(error)
    else:
        print_success("V4 list_workspace_files -> update_todo -> text 通过")
    return result
