#!/usr/bin/env python3
"""V4 ask_user_question interrupt/resume E2E scenario."""

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


async def run_ask_user_resume_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("ask_user_resume", scenario_config)
    print_test_header(scenario_config.get("description", "V4 ask_user_question resume"))

    session_result = await api.create_session(title="V4 ask_user_question resume")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    prompt = scenario_config.get(
        "prompt",
        "继续任务前，必须先调用 ask_user_question 询问我是否批准，"
        "选项为批准、拒绝；收到答案后直接输出包含该答案的最终总结。",
    )
    conversation_result = await api.create_conversation(session_id, prompt)
    if not conversation_result.get("success", True):
        result.errors.append(
            f"create_conversation: {conversation_result.get('message')}"
        )
        return result

    conversation_id = conversation_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_step(1, "等待交互中断...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)
    await collect_stream_output(
        api,
        conversation_id,
        result,
        verbose=verbose,
        timeout=float(scenario_config.get("interrupt_timeout", 180.0)),
    )

    awaiting = await wait_for_conversation_state(
        api,
        conversation_id,
        "awaiting_user_input",
        timeout=30.0,
    )
    if awaiting.get("data", {}).get("state") != "awaiting_user_input":
        result.errors.append("conversation_not_awaiting_user_input")
    if not result.user_input_requests:
        result.errors.append("missing_user_input_request_event")

    answer = scenario_config.get("answer", "批准")
    print_step(2, "恢复对话...", Colors.CYAN)
    resume_result = await api.resume_conversation(conversation_id, answer)
    if not resume_result.get("success", True):
        result.errors.append(f"resume: {resume_result.get('message')}")
        return result

    completed = await wait_for_conversation_state(
        api,
        conversation_id,
        "completed",
        timeout=float(scenario_config.get("completion_timeout", 180.0)),
    )
    result.response_text = (
        resume_result.get("data", {}).get("final_reply")
        or extract_response_text(completed)
    )
    if not result.response_text.strip():
        result.errors.append("empty_response_after_resume")

    if result.errors:
        for error in result.errors:
            print_error(error)
    else:
        print_success("ask_user_question 中断、事件、resume 与 completed 均通过")
    return result
