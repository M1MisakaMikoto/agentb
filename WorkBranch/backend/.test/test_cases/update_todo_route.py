#!/usr/bin/env python3
"""
update_todo Route Bug 验证测试

验证 agent 调用 update_todo 后是否被 check_state_v3 误杀退出。
触发路径：list_workspace_files → update_todo → [预期：应继续执行，不应提前结束]
"""

import asyncio
import json
import re

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


# 用户消息：引导 agent 依次调用 list_workspace_files → update_todo → chat
USER_PROMPT = (
    "请帮我完成以下任务：先列出工作区文件，然后创建一个待办清单（包含读取文件、分析病害、输出对比报告三个步骤），"
    "最后用 chat 工具汇总结果回复我。创建待办清单时请使用 update_todo 工具。"
)


async def run_update_todo_route_test(
    api: APIClient, scenario_config: dict, verbose: bool = True
) -> TestResult:
    result = TestResult("update_todo_route", scenario_config)

    print_test_header("update_todo route_after_execute 路由验证")

    # === Step 1: 创建 Session + Conversation ===
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="update_todo_route_test")
    if not session_result.get("success", True):
        print_error(f"Failed: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result["data"]["id"]
    print_success(f"Session: {session_id}")

    print_step(2, "Sending prompt (list_workspace_files + update_todo)...", Colors.CYAN)
    conv_result = await api.create_conversation(session_id, USER_PROMPT)
    if not conv_result.get("success", True):
        print_error(f"Failed: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result["data"]["conversation_id"]
    print_success(f"Conversation: {conversation_id}")

    # === Step 3: 等待处理开始并收集流式输出 ===
    print_step(3, "Waiting for processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)

    print_step(4, "Collecting stream (watching for route_after_execute traces)...", Colors.CYAN)

    # 用文件记录流式日志以便分析
    from pathlib import Path
    import time as _time
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream_log = str(log_dir / f"update_todo_route_{_time.strftime('%H%M%S')}.log")

    await collect_stream_output(
        api, conversation_id, result, verbose=verbose, stream_log_file=stream_log
    )

    print_step(5, "Waiting for completion...", Colors.CYAN)
    final_result = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=120.0
    )
    result.response_text = extract_response_text(final_result)

    # === Step 6: 分析路由日志 ===
    print_step(6, "Analyzing route traces...", Colors.CYAN)

    # 从流式日志中提取 route_after_execute 的 trace 行
    route_traces = []
    try:
        with open(stream_log, "r", encoding="utf-8") as f:
            for line in f:
                if "[route_after_execute]" in line and ("ENTER" in line or "->" in line):
                    route_traces.append(line.strip())
    except FileNotFoundError:
        pass

    # 也尝试从 backend console 日志找（如果有的话）
    if not route_traces:
        # fallback: 搜索最近的 backend 日志
        backend_logs = sorted(log_dir.glob("backend_console_*.log"), reverse=True)
        if backend_logs:
            with open(backend_logs[0], "r", encoding="utf-8") as f:
                content = f.read()
                route_traces = re.findall(
                    r".*?\[route_after_execute\].*", content
                )

    # === 断言 ===
    bugs_found = []

    if route_traces:
        print_success(f"Found {len(route_traces)} route trace lines")
        for t in route_traces:
            print(f"  {Colors.DIM}{t[:150]}{Colors.ENDC}")

        # 检查是否有 update_todo 后的 trace
        after_update_todo = False
        for i, trace in enumerate(route_traces):
            if "last_tool=update_todo" in trace or "tool=update_todo" in trace:
                after_update_todo = True
                # 看后续路由决策
                if i + 1 < len(route_traces):
                    next_route = route_traces[i + 1]
                    if "check_state_v3" in next_route:
                        bugs_found.append(
                            "BUG CONFIRMED: update_todo 后进入 check_state_v3 "
                            "(应在 pending=[] 时短路回 decide)"
                        )
                        print_error(next_route)
                    elif "decide" in next_route:
                        print_success(f"OK: update_todo → decide (正确)")
                    else:
                        print(f"  ? {next_route[:120]}")
    else:
        print_error("No route_after_execute traces found!")
        print_error(f"Check log file: {stream_log}")
        bugs_found.append("无法获取路由日志 — 请确认诊断日志已生效")

    # === Step 7: 最终判定 ===
    print_step(7, "Verdict...", Colors.CYAN)

    # 检查工具调用序列
    tool_calls = getattr(result, "tool_calls", [])
    has_list_workspace = "list_workspace_files" in tool_calls
    has_update_todo = "update_todo" in tool_calls
    has_chat = "chat" in tool_calls

    print(f"\n  Tool calls: {tool_calls}")
    print(f"  list_workspace_files: {'YES' if has_list_workspace else 'NO'}")
    print(f"  update_todo:          {'YES' if has_update_todo else 'NO'}")
    print(f"  chat:                 {'YES' if has_chat else 'NO'}")

    if has_list_workspace and has_update_todo and not has_chat:
        # agent 执行了 list + update_todo 但没到 chat → 很可能是被误杀了
        if not bugs_found:
            bugs_found.append(
                "SYMPTOM: 有 list+update_todo 无 chat，但无路由日志佐证"
            )
    elif has_list_workspace and has_update_todo and has_chat:
        print_success("Agent 完整执行了 list → update_todo → chat (正常)")

    if bugs_found:
        for b in bugs_found:
            print_error(b)
        result.errors.extend(bugs_found)
    else:
        print_success("No routing anomaly detected")

    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  update_todo Route Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}  Stream log: {stream_log}{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

    return result
