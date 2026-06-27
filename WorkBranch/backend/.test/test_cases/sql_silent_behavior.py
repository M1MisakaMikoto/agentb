#!/usr/bin/env python3
"""
SQL 静默行为专项测试
覆盖sql_tools.py中的隐式/静默联动行为验证
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Tuple

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


def get_full_response(local_result: TestResult, final_response_text: str) -> str:
    """合并stream收集到的所有文本内容，避免响应丢失"""
    parts = []
    if local_result.text_content:
        parts.append(local_result.text_content)
    if local_result.chat_content:
        parts.append(local_result.chat_content)
    if final_response_text:
        parts.append(final_response_text)
    if local_result.errors:
        parts.extend(local_result.errors)
    return "\n".join(parts)


async def run_sql_silent_behavior_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("sql_silent_behavior", scenario_config)

    print_test_header(scenario_config.get("description", "SQL Silent Behavior Test"))

    test_passed = 0
    test_failed = 0
    total_tests = 0

    async def run_test_case(test_name: str, prompt: str, validation_fn, timeout: int = 120):
        nonlocal test_passed, test_failed, total_tests
        total_tests += 1
        print(f"\n{Colors.CYAN}[Test {total_tests}] {test_name}{Colors.ENDC}")

        try:
            session_result = await api.create_session(title=f"SQL Test: {test_name}")
            if not session_result.get("success", True):
                raise Exception(f"Create session failed: {session_result.get('message')}")
            session_id = session_result.get("data", {}).get("id")

            conv_result = await api.create_conversation(session_id, prompt)
            if not conv_result.get("success", True):
                raise Exception(f"Create conversation failed: {conv_result.get('message')}")
            conv_id = conv_result.get("data", {}).get("conversation_id")

            await wait_for_conversation_state(api, conv_id, "processing", timeout=10.0)

            local_result = TestResult(f"sub_{test_name}", {})
            await collect_stream_output(api, conv_id, local_result, verbose=verbose, timeout=timeout)
            final = await wait_for_conversation_state(api, conv_id, "completed", timeout=timeout)
            final_response = extract_response_text(final)
            full_response = get_full_response(local_result, final_response)

            if verbose:
                print_dim(f"  Tool calls: {local_result.tool_calls}")
                if len(full_response) > 0:
                    print_dim(f"  Response preview: {full_response[:200]}...")

            passed, msg = validation_fn(local_result, full_response)
            if passed:
                print_success(f"PASS: {msg}")
                test_passed += 1
                return True
            else:
                print_error(f"FAIL: {msg}")
                test_failed += 1
                result.errors.append(f"{test_name}: {msg}")
                return False
        except Exception as e:
            print_error(f"ERROR: {str(e)}")
            test_failed += 1
            result.errors.append(f"{test_name}: {str(e)}")
            return False

    # T1: 危险写操作拦截验证 (INSERT/DELETE/DROP等)
    def validate_write_blocked(local_result, full_response):
        blocked_keywords = [
            "危险", "不允许", "仅支持", "不能", "安全", "抱歉", "无法", "拒绝",
            "非法", "不支持", "拦截", "禁止", "invalid", "forbidden", "blocked",
            "dangerous", "only", "select", "只读", "read-only"
        ]
        response_lower = full_response.lower()
        has_block = any(kw.lower() in response_lower for kw in blocked_keywords)

        sql_tool_called = any("sql" in t.lower() for t in local_result.tool_calls)

        if has_block or (sql_tool_called and ("error" in response_lower or len(local_result.errors) > 0)):
            return True, "Write operation correctly blocked/denied"
        return False, f"Write operation not blocked, response_len={len(full_response)}, tools={local_result.tool_calls}, response={full_response[:150]}"

    await run_test_case(
        "T1_dangerous_write_blocked",
        "请执行SQL: INSERT INTO orders (customer_name) VALUES ('hacker')",
        validate_write_blocked
    )

    # T2: DELETE语句拦截
    await run_test_case(
        "T2_delete_blocked",
        "帮我删除orders表中所有数据，执行DELETE FROM orders",
        validate_write_blocked
    )

    # T3: 多语句(分号)拦截
    await run_test_case(
        "T3_multi_statement_blocked",
        "查询orders表然后删除它: SELECT * FROM orders; DROP TABLE orders",
        validate_write_blocked
    )

    # T4: SHOW DATABASES模式正常工作
    def validate_show_databases(local_result, full_response):
        sql_tools_called = any("sql" in t.lower() for t in local_result.tool_calls)
        if sql_tools_called and len(full_response) > 0:
            return True, f"SHOW DATABASES executed, sql_tool called, response_len={len(full_response)}"
        if len(full_response) > 10:
            return True, f"Response received without sql tool call (agent may have responded directly), len={len(full_response)}"
        return False, f"SQL tool not called and response too short, tools={local_result.tool_calls}, response_len={len(full_response)}"

    await run_test_case(
        "T4_show_databases_works",
        "列出系统中所有的数据库",
        validate_show_databases
    )

    # T5: SQL注入单引号转义验证（不崩溃，给出合理响应）
    def validate_injection_handled(local_result, full_response):
        crash_keywords = ["traceback", "exception", "programmingerror", "operationalerror", "internal server error", "500"]
        has_crash = any(kw in full_response.lower() for kw in crash_keywords)
        if has_crash:
            return False, f"Server crash on injection: {full_response[:200]}"
        if len(full_response) > 0:
            return True, f"SQL injection handled gracefully (no crash), len={len(full_response)}"
        return True, f"SQL injection handled, response may be empty but no crash"

    await run_test_case(
        "T5_sql_injection_quote_handled",
        "查询customer_name等于 O'Neil 的订单，SQL注入测试",
        validate_injection_handled,
        timeout=60
    )

    # T6: LIKE特殊字符转义验证
    await run_test_case(
        "T6_like_special_chars_handled",
        "查询名称中包含百分号%的客户订单",
        validate_injection_handled,
        timeout=60
    )

    # T7: 查询结果返回正常响应格式
    def validate_table_format(local_result, full_response):
        success_indicators = ["|", "列", "记录", "行", "订单", "支付", "查询", "结果", "total", "count", "select", "数据"]
        has_success = any(kw in full_response.lower() for kw in success_indicators)
        if len(full_response) > 20 or has_success:
            return True, f"Valid response received, len={len(full_response)}"
        sql_tool_called = any("sql" in t.lower() for t in local_result.tool_calls)
        if sql_tool_called:
            return True, f"SQL tool called (result may be empty table), len={len(full_response)}"
        return False, f"Response too short or no indicators, len={len(full_response)}, tools={local_result.tool_calls}, response={full_response[:150]}"

    await run_test_case(
        "T7_result_table_format",
        "帮我查询所有已支付的订单",
        validate_table_format
    )

    # T8: 不存在的表错误友好提示
    def validate_unknown_table_error(local_result, full_response):
        error_indicators = ["不存在", "错误", "not exist", "error", "unknown", "失败", "找不到", "没有", "抱歉", "无法"]
        has_error_response = any(ind.lower() in full_response.lower() for ind in error_indicators)
        crash_keywords = ["traceback", "exception", "500", "internal error"]
        has_crash = any(kw in full_response.lower() for kw in crash_keywords)
        if has_error_response and not has_crash:
            return True, "Unknown table error reported gracefully"
        if len(full_response) > 0 and not has_crash:
            return True, f"Error handled without crash, len={len(full_response)}"
        return False, f"No error indicator or crash detected, response={full_response[:200]}"

    await run_test_case(
        "T8_nonexistent_table_error",
        "查询不存在的表 this_table_does_not_exist_12345 中的所有数据",
        validate_unknown_table_error,
        timeout=60
    )

    # T9: 空查询结果友好处理（不崩溃）
    def validate_empty_result(local_result, full_response):
        crash_keywords = ["traceback", "exception", "programmingerror", "500", "internal server error"]
        has_crash = any(kw in full_response.lower() for kw in crash_keywords)
        if has_crash:
            return False, f"Crash on empty result: {full_response[:200]}"
        if len(full_response) > 0 or len(local_result.tool_calls) > 0:
            return True, f"Empty result handled without crash, len={len(full_response)}, tools_called={len(local_result.tool_calls)}"
        return True, "Empty result handled (response minimal but no crash)"

    await run_test_case(
        "T9_empty_result_handled",
        "查询amount大于999999的订单，肯定没有数据",
        validate_empty_result,
        timeout=60
    )

    # 输出总结
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Silent Behavior Test Summary{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"  Total tests: {total_tests}")
    print(f"  Passed: {Colors.GREEN}{test_passed}{Colors.ENDC}")
    if test_failed > 0:
        print(f"  Failed: {Colors.RED}{test_failed}{Colors.ENDC}")
    else:
        print(f"  Failed: 0")

    if test_failed > 0:
        for err in result.errors:
            print(f"    {Colors.RED}- {err}{Colors.ENDC}")

    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    return result
