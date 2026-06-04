#!/usr/bin/env python3
"""
Bridge Defect Extraction Test

从桥梁检测报告中提取病害数据
测试文件: .dev/table/桥梁检测报告/2020/07 朝阳寺立交桥.doc
"""

import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

from .base import (
    APIClient,
    TestResult,
    Colors,
    get_project_root,
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


# ============================================================
# 测试配置
# ============================================================

# 测试文件路径 - 朝阳寺立交桥 2020年检测报告
TEST_FILE_PATH = Path(".dev/table/桥梁检测报告/2020/07 朝阳寺立交桥.doc")

# 病害提取提示词
DEFECT_EXTRACTION_PROMPT = """请分析这份桥梁检测报告，提取所有病害信息。

需要提取的病害数据包括：
1. **上部结构病害**: 梁体裂缝、破损、露筋、蜂窝麻面等
2. **下部结构病害**: 桥墩裂缝、破损、冲刷等
3. **桥面系病害**: 桥面铺装裂缝、破损、伸缩缝损坏、护栏损坏等
4. **支座病害**: 支座脱空、变形、损坏等

请以结构化的格式输出，分类整理每类病害的具体位置、类型、严重程度等信息。
如果报告中没有某类病害，请标注"未发现"。
"""


# ============================================================
# 工具函数
# ============================================================

def resolve_source_file(file_path: Path) -> Path:
    """解析源文件路径，支持相对路径和绝对路径"""
    if file_path.is_absolute():
        return file_path if file_path.exists() else None
    root = get_project_root()
    return (root / file_path).resolve()


def extract_defect_summary(text: str) -> Dict[str, Any]:
    """从提取结果中提取病害摘要"""
    result = {
        "has_upper_structure": False,
        "has_lower_structure": False,
        "has_deck": False,
        "has_bearing": False,
        "upper_defects": [],
        "lower_defects": [],
        "deck_defects": [],
        "bearing_defects": [],
        "total_defect_count": 0,
    }

    if not text:
        return result

    # 检测是否有各类结构病害
    upper_keywords = ["上部结构", "梁体", "主梁", "横梁", "纵梁"]
    lower_keywords = ["下部结构", "桥墩", "桥台", "基础", "承台"]
    deck_keywords = ["桥面", "桥面系", "铺装", "伸缩缝", "护栏", "人行道"]
    bearing_keywords = ["支座", "垫石", "锚栓"]

    text_lower = text.lower()

    for kw in upper_keywords:
        if kw in text:
            result["has_upper_structure"] = True
            break

    for kw in lower_keywords:
        if kw in text:
            result["has_lower_structure"] = True
            break

    for kw in deck_keywords:
        if kw in text:
            result["has_deck"] = True
            break

    for kw in bearing_keywords:
        if kw in text:
            result["has_bearing"] = True
            break

    # 统计病害数量（通过计数关键描述词）
    defect_indicators = ["裂缝", "破损", "损坏", "病害", "缺陷", "缺失", "脱落", "锈蚀", "露筋"]
    for indicator in defect_indicators:
        count = text.count(indicator)
        result["total_defect_count"] += count

    return result


def validate_extraction_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """验证提取结果的完整性"""
    validation = {
        "valid": True,
        "issues": [],
        "score": 0,
    }

    # 检查关键字段是否存在
    checks = [
        ("has_upper_structure", "上部结构信息"),
        ("has_lower_structure", "下部结构信息"),
        ("has_deck", "桥面系信息"),
    ]

    found_count = sum(1 for key, _ in checks if result.get(key, False))
    total_checks = len(checks)

    if found_count == 0:
        validation["valid"] = False
        validation["issues"].append("未提取到任何结构病害信息")
    elif found_count < total_checks:
        validation["issues"].append(f"缺少{total_checks - found_count}类结构信息")

    # 计算完整性得分 (0-100)
    validation["score"] = int(found_count / total_checks * 100)

    return validation


# ============================================================
# 主测试函数
# ============================================================

async def upload_report_file(api: APIClient, workspace_id: str, file_path: Path, verbose: bool = True) -> Optional[str]:
    """上传检测报告文件"""
    full_path = resolve_source_file(file_path)

    if not full_path or not full_path.exists():
        print_error(f"File not found: {file_path}")
        return None

    try:
        upload_result = await api.upload_workspace_file(workspace_id, full_path)

        if upload_result.get("success", False):
            data = upload_result.get("data", {})
            uploaded_files = data.get("uploaded", []) if isinstance(data, dict) else []

            if uploaded_files:
                filename = uploaded_files[0].get("name") if isinstance(uploaded_files[0], dict) else str(uploaded_files[0])
                if verbose:
                    print_success(f"Uploaded: {filename}")
                return filename
        else:
            print_error(f"Upload failed: {upload_result.get('message', 'Unknown error')}")
            return None

    except Exception as e:
        print_error(f"Error uploading file: {e}")
        return None


async def run_defect_extraction_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
    file_path: Path = TEST_FILE_PATH,
) -> TestResult:
    """
    运行桥梁病害提取测试

    Args:
        api: API客户端
        scenario_config: 场景配置
        verbose: 是否显示详细信息
        file_path: 要分析的检测报告文件路径
    """
    result = TestResult("bridge_defect_extract", scenario_config)
    result.file_path = str(file_path)

    stream_log_dir = get_project_root() / "logs" / "e2e_stream_traces"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stream_log_file = str(stream_log_dir / f"bridge_defect_extract_{timestamp}.log")

    print_test_header(scenario_config.get(
        "description",
        f"Bridge Defect Extraction Test - {file_path.name}"
    ))

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    output_log = log_dir / f"bridge_defect_extract_{timestamp}.md"

    # 检查文件是否存在
    print_step(1, f"Validating report file: {file_path.name}...", Colors.CYAN)
    full_path = resolve_source_file(file_path)

    if not full_path or not full_path.exists():
        error_msg = f"Report file not found: {file_path}"
        print_error(error_msg)
        result.errors.append(error_msg)
        return result

    print_success(f"File found: {full_path}")

    print_step(2, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title=f"Bridge Defect Extraction - {file_path.stem}")

    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    result.workspace_id = workspace_id
    print_success(f"Session created: {session_id}")
    print_dim(f"Workspace ID: {workspace_id}")

    print_step(3, f"Uploading report file: {file_path.name}...", Colors.CYAN)
    uploaded_filename = await upload_report_file(api, workspace_id, file_path, verbose=verbose)

    if not uploaded_filename:
        error_msg = "Failed to upload report file"
        print_error(error_msg)
        result.errors.append(error_msg)
        return result

    print_success(f"Report file uploaded: {uploaded_filename}")

    print_step(4, "Creating conversation with defect extraction prompt...", Colors.CYAN)

    # 构建提示词
    prompt = scenario_config.get("prompt", DEFECT_EXTRACTION_PROMPT)

    conv_result = await api.create_conversation(session_id, prompt)

    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")

    print_step(5, "Waiting for conversation to start processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)

    print_step(6, "Streaming extraction response...", Colors.CYAN)
    extraction_timeout = scenario_config.get("extraction_timeout", 300.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=extraction_timeout, stream_log_file=stream_log_file)

    # 处理后续对话（PLAN模式等）
    max_followups = 3
    followup_count = 0
    while (result.next_conversation_id or result.detected_mode == "PLAN") and followup_count < max_followups:
        followup_count += 1
        if result.next_conversation_id:
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] Auto-approved plan detected{Colors.ENDC}")
            next_conv_id = result.next_conversation_id
            result.next_conversation_id = None
            conversation_id = next_conv_id
            result.conversation_id = conversation_id
            await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
            await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=extraction_timeout, stream_log_file=stream_log_file)
        elif result.detected_mode == "PLAN":
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] PLAN mode detected, sending approval...{Colors.ENDC}")
            try:
                approve_result = await api.approve_plan(result.workspace_id, approved=True)
                if verbose:
                    print(f"{Colors.DIM}[Follow-up #{followup_count}] Plan approval result: {approve_result.get('message', 'ok')}{Colors.ENDC}")
            except Exception as e:
                if verbose:
                    print(f"{Colors.DIM}[Follow-up #{followup_count}] Approve plan failed: {e}{Colors.ENDC}")
            conv_result = await api.create_conversation(session_id, "可以")
            if conv_result.get("success", True):
                next_conv_id = conv_result.get("data", {}).get("conversation_id")
                if next_conv_id:
                    conversation_id = next_conv_id
                    result.conversation_id = conversation_id
                    result.detected_mode = None
                    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
                    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=extraction_timeout, stream_log_file=stream_log_file)

    if followup_count > 0 and verbose:
        print_success(f"Completed {followup_count} follow-up conversation(s)")

    # 检查流输出状态
    print_step(6.5, "Checking stream output status...", Colors.CYAN)

    conv_check = await api.get_conversation(conversation_id)
    current_state = conv_check.get("data", {}).get("state")

    if current_state == "running" and not result.response_text:
        if verbose:
            print(f"{Colors.YELLOW}[Recovery] Stream interrupted, waiting for completion...{Colors.ENDC}")
        extra_wait_timeout = min(300.0, extraction_timeout)
        recovery_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=extra_wait_timeout
        )
        recovery_state = recovery_result.get("data", {}).get("state")
        result.response_text = extract_response_text(recovery_result)

        if result.response_text and verbose:
            print_success(f"[Recovery] Captured response ({len(result.response_text)} chars)")
    elif current_state == "completed":
        if verbose:
            print_success("[Recovery] Conversation already completed")
        result.response_text = extract_response_text(conv_check)

    # 等待会话完成
    print_step(7, "Waiting for conversation to complete...", Colors.CYAN)

    max_completion_wait = 600
    completion_wait_start = time.time()

    while time.time() - completion_wait_start < max_completion_wait:
        final_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=60.0
        )
        if not final_result:
            await asyncio.sleep(10)
            continue
        final_state = final_result.get("data", {}).get("state") if isinstance(final_result, dict) else None

        if final_state == "completed":
            result.response_text = extract_response_text(final_result)
            break
        elif final_state == "failed":
            result.errors.append("Conversation failed")
            break

        await asyncio.sleep(10)

    # 分析提取结果
    print_step(8, "Analyzing extraction results...", Colors.CYAN)

    if result.response_text:
        defect_summary = extract_defect_summary(result.response_text)
        result.defect_summary = defect_summary

        validation = validate_extraction_result(defect_summary)
        result.validation = validation

        if verbose:
            print(f"{Colors.CYAN}Extraction Summary:{Colors.ENDC}")
            print(f"  - Upper Structure Defects: {'Yes' if defect_summary['has_upper_structure'] else 'No'}")
            print(f"  - Lower Structure Defects: {'Yes' if defect_summary['has_lower_structure'] else 'No'}")
            print(f"  - Deck Defects: {'Yes' if defect_summary['has_deck'] else 'No'}")
            print(f"  - Bearing Defects: {'Yes' if defect_summary['has_bearing'] else 'No'}")
            print(f"  - Total Defect Mentions: {defect_summary['total_defect_count']}")
            print(f"  - Completeness Score: {validation['score']}%")

            if validation['issues']:
                for issue in validation['issues']:
                    print_warning(f"  - {issue}")
    else:
        result.errors.append("No response text captured")
        print_error("No extraction result captured")

    # 保存输出日志
    if result.response_text:
        with open(output_log, "w", encoding="utf-8") as f:
            f.write(f"# Bridge Defect Extraction Test Results\n\n")
            f.write(f"**Test Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"**Report File**: {file_path.name}\n\n")
            f.write(f"**File Path**: {file_path}\n\n")
            f.write(f"**Session ID**: {result.session_id}\n\n")
            f.write(f"**Conversation ID**: {result.conversation_id}\n\n")
            f.write("---\n\n")
            f.write(f"## Extraction Result\n\n")
            f.write(result.response_text)
            f.write(f"\n\n---\n\n")
            f.write(f"## Summary\n\n")
            f.write(f"- Completeness Score: {validation['score']}%\n")
            f.write(f"- Total Defect Mentions: {defect_summary['total_defect_count']}\n")

        if verbose:
            print_success(f"Output saved: {output_log}")

    result.done = True
    return result


# ============================================================
# 入口点
# ============================================================

async def main():
    """独立运行测试"""
    import argparse

    parser = argparse.ArgumentParser(description="Bridge Defect Extraction Test")
    parser.add_argument("--config", default=None, help="Config file path")
    parser.add_argument("--file", default=None, help="Report file path")
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

    api_url = config.get("api_url", "http://127.0.0.1:8000")
    api = APIClient(base_url=api_url)

    # 检查后端
    from .base import wait_for_backend
    if not wait_for_backend(port=args.port):
        print_error("Backend not available")
        return 1

    # 执行测试
    file_path = Path(args.file) if args.file else TEST_FILE_PATH
    scenario_config = config.get("scenarios", {}).get("bridge_defect_extract", {})

    result = await run_defect_extraction_test(
        api,
        scenario_config,
        verbose=args.verbose,
        file_path=file_path,
    )

    # 输出结果
    print("\n" + "=" * 60)
    print(f"{Colors.BOLD}Test Result Summary{Colors.ENDC}")
    print("=" * 60)
    print(f"Scenario: {result.scenario}")
    print(f"File: {result.file_path}")
    print(f"Session ID: {result.session_id}")
    print(f"Errors: {len(result.errors)}")

    if hasattr(result, 'validation'):
        print(f"Completeness Score: {result.validation['score']}%")

    if result.errors:
        print(f"{Colors.RED}Errors:{Colors.ENDC}")
        for error in result.errors:
            print(f"  - {error}")

    return 0 if not result.errors else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)