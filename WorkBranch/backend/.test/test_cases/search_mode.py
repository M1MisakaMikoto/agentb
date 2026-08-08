#!/usr/bin/env python3
"""V4 explore subagent E2E scenario."""

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


async def run_search_mode_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("search_mode", scenario_config)
    print_test_header(scenario_config.get("description", "V4 explore subagent"))

    session_result = await api.create_session(title="V4 explore subagent")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    question = scenario_config.get(
        "question",
        "请调用 call_explore_agent 搜索市政设施管理规定，"
        "由探索子代理使用 explore_internet，最后输出总结。",
    )
    conversation_result = await api.create_conversation(session_id, question)
    if not conversation_result.get("success", True):
        result.errors.append(
            f"create_conversation: {conversation_result.get('message')}"
        )
        return result

    conversation_id = conversation_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_step(1, "等待探索子代理完成...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=300.0)
    completed = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=300.0
    )
    result.response_text = extract_response_text(completed)

    observed = {"call_explore_agent", "explore_internet"}.intersection(result.tool_calls)
    if not observed:
        result.errors.append(f"explore_tool_not_observed:{result.tool_calls}")
    if not result.response_text.strip():
        result.errors.append("empty_explore_response")

    if result.errors:
        for error in result.errors:
            print_error(error)
    else:
        print_success(f"探索工具链通过: {sorted(observed)}")
    return result
