#!/usr/bin/env python3
"""V4 SQL failure propagation and final-summary E2E scenario."""

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


INVALID_USER_ID = 999999999


def _latest_tool_result(tool_results: list, tool_name: str) -> dict:
    for entry in reversed(tool_results):
        if entry.get("tool_name") == tool_name:
            return entry
    return {}


async def run_sql_permission_fallback_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("sql_permission_fallback", scenario_config)
    print_test_header(
        scenario_config.get("description", "V4 SQL failure propagation")
    )
    invalid_api = APIClient(api.config, user_id=INVALID_USER_ID)

    session_result = await invalid_api.create_session(title="V4 SQL failure propagation")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    prompt = scenario_config.get(
        "prompt",
        "请调用 sql_query 查询大渡口区设施数量；若工具失败，"
        "请依据工具错误直接输出包含失败原因的最终总结。",
    )
    conversation_result = await invalid_api.create_conversation(session_id, prompt)
    if not conversation_result.get("success", True):
        result.errors.append(
            f"create_conversation: {conversation_result.get('message')}"
        )
        return result

    conversation_id = conversation_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_step(1, "触发 SQL 错误并等待 V4 总结...", Colors.CYAN)
    await wait_for_conversation_state(
        invalid_api, conversation_id, "processing", timeout=15.0
    )
    timeout = float(scenario_config.get("timeout", 240.0))
    await collect_stream_output(
        invalid_api,
        conversation_id,
        result,
        verbose=verbose,
        timeout=timeout,
    )
    completed = await wait_for_conversation_state(
        invalid_api, conversation_id, "completed", timeout=timeout
    )
    result.response_text = extract_response_text(completed)

    if "sql_query" not in result.tool_calls:
        result.errors.append("missing_sql_query_call")
    sql_result = _latest_tool_result(result.tool_results, "sql_query")
    sql_error = str(sql_result.get("error") or "")
    if not sql_error:
        result.errors.append("missing_sql_failure_record")
    if not result.response_text.strip():
        result.errors.append("empty_summary_after_sql_failure")

    if result.errors:
        for error in result.errors:
            print_error(error)
    else:
        print_success("V4 SQL failed record 与最终错误总结通过")
    return result
