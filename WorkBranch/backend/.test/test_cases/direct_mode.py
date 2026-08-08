#!/usr/bin/env python3
"""V4 final text response E2E scenario."""

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


async def run_direct_mode_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("direct_mode", scenario_config)
    print_test_header(scenario_config.get("description", "V4 text response"))

    session_result = await api.create_session(title="V4 text response")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    question = scenario_config.get(
        "question",
        "请简要介绍 Python 异步编程模型，完成后直接输出最终总结。",
    )
    conversation_result = await api.create_conversation(session_id, question)
    if not conversation_result.get("success", True):
        result.errors.append(
            f"create_conversation: {conversation_result.get('message')}"
        )
        return result

    conversation_id = conversation_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_step(1, "等待 V4 最终文本...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    completed = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=180.0
    )
    result.response_text = (
        extract_response_text(completed)
        or result.text_content
        or result.chat_content
    )

    if not result.response_text.strip():
        result.errors.append("empty_v4_text_response")
    if "chat" in result.tool_calls:
        result.errors.append("retired_chat_tool_called")

    if result.errors:
        for error in result.errors:
            print_error(error)
    else:
        print_success("V4 type=text 最终回复通过")
    return result
