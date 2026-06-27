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
            response_text = extract_response_text(final)

            passed, msg = validation_fn(local_result, response_text)
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
    def validate_write_blocked(local_result, response_text):
        blocked_keywords = ["危险", "不允许", "仅支持", "错误", "不能", "安全"]
        combined = (response_text or "") + " ".join(local_result.errors)
        has_block = any(kw in combined for kw in blocked_keywords)
        if has_block:
            return True, "Write operation correctly blocked"
        return False, f"Write operation not blocked, response: {response_text[:200]}"

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
    def validate_show_databases(local_result, response_text):
        sql_tools_called = any("sql" in t.lower() for t in local_result.tool_calls)
        if sql_tools_called and len(response_text) > 0:
            return True, f"SHOW DATABASES executed successfully, response={len(response_text)} chars"
        return False, f"SQL tool not called or empty response, tools={local_result.tool_calls}"

    await run_test_case(
        "T4_show_databases_works",
        "列出系统中所有的数据库",
        validate_show_databases
    )

    # T5: SQL注入单引号转义验证（不导致错误）
    def validate_injection_handled(local_result, response_text):
        has_error = any("error" in e.lower() or "错误" in e for e in local_result.errors)
        if not has_error and len(response_text) > 0:
            return True, "SQL injection with quotes handled gracefully"
        return False, f"Error handling injection, errors={local_result.errors}"

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

    # T7: 查询结果返回正常表格格式
    def validate_table_format(local_result, response_text):
        if "|" in response_text or "列" in response_text or "记录" in response_text or "行" in response_text or len(response_text) > 50:
            return True, f"Response contains formatted results, length={len(response_text)}"
        return False, f"Unexpected response format: {response_text[:300]}"

    await run_test_case(
        "T7_result_table_format",
        "帮我查询所有已支付的订单，返回表格",
        validate_table_format
    )

    # T8: 不存在的表错误友好提示
    def validate_unknown_table_error(local_result, response_text):
        error_indicators = ["不存在", "错误", "not exist", "error", "Unknown", "失败"]
        combined = (response_text or "").lower() + " ".join(local_result.errors).lower()
        if any(ind.lower() in combined for ind in error_indicators) or len(local_result.errors) > 0:
            return True, "Unknown table error reported gracefully"
        return False, f"Error not reported for non-existent table, response={response_text[:200]}"

    await run_test_case(
        "T8_nonexistent_table_error",
        "查询不存在的表 this_table_does_not_exist_12345 中的所有数据",
        validate_unknown_table_error,
        timeout=60
    )

    # T9: 空查询结果友好处理
    def validate_empty_result(local_result, response_text):
        no_error = len(local_result.errors) == 0
        if no_error and len(response_text) > 0:
            return True, "Empty/zero results handled without exception"
        return False, f"Error with empty result set, errors={local_result.errors}"

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
