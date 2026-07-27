#!/usr/bin/env python3
"""
SQL 工具失败回退测试 (Bug: director_agent tool_history 丢失 error 字段)

背景:
    director_agent.py 构建 tool_history 时只存 result, 丢失 error 字段,
    导致 graph_prompts._format_tool_history 的 error 展示逻辑失效,
    LLM 看不到 SQL 错误, 误判返回 step_done, 未按规则14回退读工作区文件。

修复:
    director_agent.py:2018 补 "error": tool_result.get("error") 字段。

验证:
    用无效 user_id (999999999) 触发 SQL 工具返回 error (BTManager 不可用或用户不存在),
    断言 agent 在 SQL 失败后按规则14回退调用 list_workspace_files,
    而非提前 step_done 结束。
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from .base import (
    APIClient,
    TestResult,
    Colors,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    print_warning,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


# 无效 user_id: BTManager 中不存在此用户, 触发权限拒绝;
# 若 BTManager 不可用, 则触发连接错误。两者都返回 error, 均可验证修复。
INVALID_USER_ID = 999999999

# 明确指定"查询数据库", 触发 agent 调用 sql_query (避免 agent 直接读工作区文件)
PROMPT = "请查询数据库，大渡口区有多少座设施"


def _find_tool_result(tool_results: list, tool_name: str) -> dict:
    """从 tool_results 列表中查找指定工具的最近一次结果"""
    for entry in reversed(tool_results):
        if entry.get("tool_name") == tool_name:
            return entry
    return {}


async def run_sql_permission_fallback_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """运行 SQL 失败回退测试

    用无效 user_id 创建独立 APIClient, 触发 SQL error,
    验证 agent 按规则14回退到 list_workspace_files。
    """
    result = TestResult("sql_permission_fallback", scenario_config)

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print_test_header(scenario_config.get(
        "description",
        "SQL 工具失败回退测试 (验证 tool_history error 字段修复)"
    ))

    # 用无效 user_id 创建独立 APIClient, 触发 SQL 权限拒绝/连接错误
    invalid_api = APIClient(api.config, user_id=INVALID_USER_ID)
    print_step(1, f"使用无效 user_id={INVALID_USER_ID} 触发 SQL error...", Colors.CYAN)
    print_dim(f"prompt: {PROMPT}")

    # 创建 session
    session_result = await invalid_api.create_session(title="sql_permission_fallback 测试")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    result.workspace_id = workspace_id

    # 创建 conversation
    conv_result = await invalid_api.create_conversation(session_id, PROMPT)
    if not conv_result.get("success", True):
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id

    # 等待处理中
    await wait_for_conversation_state(invalid_api, conversation_id, "processing", timeout=15.0)

    # 收集流式输出
    stream_log = str(log_dir / f"sql_permission_fallback_{timestamp}.log")
    timeout = scenario_config.get("timeout", 240.0)
    await collect_stream_output(
        invalid_api, conversation_id, result,
        verbose=verbose, timeout=timeout, stream_log_file=stream_log,
    )

    # 等待完成
    completion_wait_start = time.time()
    max_wait = 300
    while time.time() - completion_wait_start < max_wait:
        final_result = await wait_for_conversation_state(invalid_api, conversation_id, "completed", timeout=60.0)
        if final_result:
            final_state = final_result.get("data", {}).get("state") if isinstance(final_result, dict) else None
            if final_state == "completed":
                if not result.response_text:
                    result.response_text = extract_response_text(final_result)
                break
            elif final_state != "running":
                if not result.response_text:
                    result.response_text = extract_response_text(final_result)
                break
        await asyncio.sleep(15)

    # ==================== 断言 ====================
    print_step(2, "验证断言...", Colors.CYAN)

    tool_calls = result.tool_calls
    tool_results = result.tool_results

    if verbose:
        print_dim(f"  tool_calls: {tool_calls}")
        print_dim(f"  tool_results count: {len(tool_results)}")
        for entry in tool_results:
            err_preview = str(entry.get("error") or "")[:80]
            print_dim(f"    - {entry.get('tool_name')}: success={entry.get('success')} err={err_preview}")

    # 断言1: agent 调用了 sql_query
    sql_called = "sql_query" in tool_calls
    if sql_called:
        print_success("OK 断言1: agent 调用了 sql_query")
    else:
        print_error("FAIL 断言1: agent 未调用 sql_query, 无法验证回退")
        result.errors.append("no_sql_query_call")

    # 断言2: sql_query 返回了 error (确认 SQL 失败被模拟)
    sql_res = _find_tool_result(tool_results, "sql_query")
    sql_error = str(sql_res.get("error") or "")
    if sql_called and sql_error:
        print_success(f"OK 断言2: sql_query 返回 error: {sql_error[:80]}")
    elif sql_called:
        print_error("FAIL 断言2: sql_query 未返回 error, 无法验证 mock 生效")
        result.errors.append("no_sql_error")

    # 断言3 (核心): agent 回退调用了 list_workspace_files (验证修复生效)
    # 修复前: tool_history 丢 error, LLM 看不到错误, 直接 step_done, 不回退
    # 修复后: tool_history 带 error, LLM 看到错误, 按规则14回退读工作区文件
    fallback_called = "list_workspace_files" in tool_calls
    if fallback_called:
        print_success("OK 断言3 (核心): agent 回退调用了 list_workspace_files (修复生效)")
    else:
        print_error("FAIL 断言3 (核心): agent 未回退到 list_workspace_files (修复未生效或 LLM 决策异常)")
        result.errors.append("no_fallback_to_workspace")

    # 断言4: agent 最终用 chat 回复或合理结束 (非提前 step_done 无回复)
    chat_called = "chat" in tool_calls
    has_response = bool(result.response_text and result.response_text.strip())
    if chat_called or has_response:
        print_success("OK 断言4: agent 最终有 chat 回复或响应文本")
    else:
        print_warning("WARN 断言4: agent 未调用 chat 且无响应文本 (可能提前 step_done)")
        # 不强制判错: 某些场景 agent 可能返回 blocked 但流里没捕获到 chat
        # 仅记录警告, 不加入 errors

    # ==================== 汇总 ====================
    print_step(3, "测试汇总...", Colors.CYAN)

    output_log = log_dir / f"sql_permission_fallback_{timestamp}.md"
    with open(output_log, "w", encoding="utf-8") as f:
        f.write("# SQL 工具失败回退测试报告\n\n")
        f.write(f"- **时间戳**: {timestamp}\n")
        f.write(f"- **session**: {result.session_id}\n")
        f.write(f"- **conversation**: {result.conversation_id}\n")
        f.write(f"- **invalid user_id**: {INVALID_USER_ID}\n")
        f.write(f"- **tool_calls**: {tool_calls}\n\n")
        f.write("## tool_results 详情\n\n")
        for entry in tool_results:
            f.write(f"- **{entry.get('tool_name')}** success={entry.get('success')} ")
            f.write(f"error={str(entry.get('error') or '')[:200]} ")
            f.write(f"result={str(entry.get('result') or '')[:200]}\n")
        f.write(f"\n## response_text\n\n{result.response_text or '(空)'}\n")
        f.write("\n## 错误\n\n")
        if result.errors:
            for e in result.errors:
                f.write(f"- {e}\n")
        else:
            f.write("无\n")

    if verbose:
        print_success(f"测试报告已保存: {output_log}")

    print(f"\n{Colors.CYAN}{'='*60}{Colors.ENDC}")
    if not result.errors:
        print(f"{Colors.GREEN}测试通过: SQL 失败后 agent 按规则14回退到 list_workspace_files{Colors.ENDC}")
    else:
        print(f"{Colors.RED}测试失败 ({len(result.errors)} 个错误):{Colors.ENDC}")
        for e in result.errors:
            print(f"   - {e}")
    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}\n")

    return result
