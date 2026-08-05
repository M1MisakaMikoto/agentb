#!/usr/bin/env python3
"""
Bridge Defect Extraction Parallel Test

双开测试：同时运行两个桥梁病害提取测试，验证 MQ bridge 的并发处理能力

测试文件:
  - 测试A: .dev/fixture/07 朝阳寺立交桥.doc
  - 测试B: .dev/fixture/09 陈家湾桥.doc
"""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

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
)

from .bridge_defect_extract import (
    run_defect_extraction_test,
    TEST_FILE_PATH,
    DEFECT_EXTRACTION_PROMPT,
)


# ============================================================
# 测试配置
# ============================================================

# 双开测试使用两个不同的文档
PARALLEL_TEST_FILE_A = Path(".dev/fixture/07 朝阳寺立交桥.doc")
PARALLEL_TEST_FILE_B = Path(".dev/fixture/09 陈家湾桥.doc")


# ============================================================
# 双开测试函数
# ============================================================

async def run_single_test(
    api: APIClient,
    scenario_config: dict,
    file_path: Path,
    test_name: str,
    verbose: bool = True,
) -> TestResult:
    """运行单个测试"""
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  [{test_name}] 启动{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")

    result = await run_defect_extraction_test(
        api,
        scenario_config,
        verbose=verbose,
        file_path=file_path,
    )

    result.scenario = f"parallel_{test_name}"
    return result


async def run_parallel_defect_extraction_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """
    运行双开桥梁病害提取测试

    同时运行两个测试：
    1. 测试1 - 使用 07 朝阳寺立交桥.doc
    2. 测试2 - 使用 09 陈家湾桥.doc

    验证 MQ bridge 在并发情况下的正确性
    """
    result = TestResult("bridge_defect_extract_parallel", scenario_config)

    print_test_header(scenario_config.get(
        "description",
        "Bridge Defect Extraction Parallel Test - 双开测试"
    ))

    # 记录开始时间
    start_time = time.time()

    # 创建两个异步任务，分别运行两个测试
    print_step(1, "准备双开测试环境...", Colors.CYAN)

    test1_task = asyncio.create_task(
        run_single_test(
            api,
            scenario_config,
            PARALLEL_TEST_FILE_A,
            "测试A - 朝阳寺立交桥",
            verbose=verbose,
        )
    )

    # 等待一小段时间再启动第二个测试，模拟真实场景
    await asyncio.sleep(2)

    test2_task = asyncio.create_task(
        run_single_test(
            api,
            scenario_config,
            PARALLEL_TEST_FILE_B,
            "测试B - 陈家湾桥",
            verbose=verbose,
        )
    )

    print_step(2, "等待两个测试完成...", Colors.CYAN)

    # 等待两个测试完成
    results = await asyncio.gather(
        test1_task,
        test2_task,
        return_exceptions=True,
    )

    test1_result = results[0]
    test2_result = results[1]

    # 处理结果
    test1_errors = []
    test2_errors = []

    if isinstance(test1_result, Exception):
        test1_errors.append(str(test1_result))
        print_error(f"测试A异常: {test1_result}")
    else:
        test1_errors = test1_result.errors
        result.test1_result = test1_result

    if isinstance(test2_result, Exception):
        test2_errors.append(str(test2_result))
        print_error(f"测试B异常: {test2_result}")
    else:
        test2_errors = test2_result.errors
        result.test2_result = test2_result

    # 计算总耗时
    duration = time.time() - start_time

    # 合并结果
    result.errors = test1_errors + test2_errors
    result.duration = duration

    # 分析结果
    print_step(3, "分析测试结果...", Colors.CYAN)

    print(f"\n{Colors.CYAN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.CYAN}  双开测试结果汇总{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*60}{Colors.ENDC}\n")

    # 测试A结果
    test1_status = "PASS" if not test1_errors else "FAIL"
    color1 = Colors.GREEN if not test1_errors else Colors.RED
    print(f"  {color1}[{test1_status}]{Colors.ENDC} 测试A (会话ID: {getattr(test1_result, 'session_id', 'N/A')})")
    if test1_errors:
        for error in test1_errors:
            print(f"         {Colors.RED}Error: {error}{Colors.ENDC}")
    elif hasattr(test1_result, 'validation'):
        print(f"         完整性得分: {test1_result.validation['score']}%")

    # 测试B结果
    test2_status = "PASS" if not test2_errors else "FAIL"
    color2 = Colors.GREEN if not test2_errors else Colors.RED
    print(f"  {color2}[{test2_status}]{Colors.ENDC} 测试B (会话ID: {getattr(test2_result, 'session_id', 'N/A')})")
    if test2_errors:
        for error in test2_errors:
            print(f"         {Colors.RED}Error: {error}{Colors.ENDC}")
    elif hasattr(test2_result, 'validation'):
        print(f"         完整性得分: {test2_result.validation['score']}%")

    print(f"\n  总耗时: {duration:.1f}秒")
    print(f"\n{Colors.CYAN}{'='*60}{Colors.ENDC}\n")

    # 判断是否通过
    # 两个测试都成功才算通过
    result.done = True

    if not test1_errors and not test2_errors:
        print_success("双开测试全部通过！MQ bridge 并发处理正常。")
    else:
        error_count = len(test1_errors) + len(test2_errors)
        print_error(f"双开测试有 {error_count} 个错误")

    return result


# ============================================================
# 入口点
# ============================================================

async def main():
    """独立运行测试"""
    import argparse

    parser = argparse.ArgumentParser(description="Bridge Defect Extraction Parallel Test")
    parser.add_argument("--config", default=None, help="Config file path")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--no-server", action="store_true", help="Skip server start")
    parser.add_argument("--port", type=int, default=8000, help="Server port")

    args = parser.parse_args()

    # 加载配置
    config = {}
    if args.config:
        from .base import load_config
        config = load_config(args.config)
    else:
        from .base import load_config
        config = load_config()

    api = APIClient(config)

    # 检查后端
    from .base import wait_for_backend
    if not wait_for_backend(port=args.port):
        print_error("Backend not available")
        return 1

    # 执行测试
    scenario_config = config.get("scenarios", {}).get("bridge_defect_extract_parallel", {})

    result = await run_parallel_defect_extraction_test(
        api,
        scenario_config,
        verbose=args.verbose,
    )

    # 输出结果
    print("\n" + "=" * 60)
    print(f"{Colors.BOLD}Test Result Summary{Colors.ENDC}")
    print("=" * 60)
    print(f"Scenario: {result.scenario}")
    print(f"Duration: {result.duration:.1f}s")
    print(f"Errors: {len(result.errors)}")

    if result.errors:
        print(f"{Colors.RED}Errors:{Colors.ENDC}")
        for error in result.errors:
            print(f"  - {error}")
        return 1
    else:
        print(f"{Colors.GREEN}All tests passed!{Colors.ENDC}")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
