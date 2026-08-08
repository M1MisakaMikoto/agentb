#!/usr/bin/env python3
"""
Qiaozitang Overpass Monthly Query Test

跨5份大渡口月度巡查报告，聚合查询"桥梓塘立交"1-5月的数据。

测试文件:
  .dev/fixture/大渡口1月巡查报告.docx
  .dev/fixture/大渡口2月巡查报告.docx
  .dev/fixture/大渡口3月巡查报告.docx
  .dev/fixture/大渡口4月巡查报告.docx
  .dev/fixture/大渡口5月巡查报告.docx
"""

from pathlib import Path
from typing import List

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

# 5 份月度巡查报告（相对项目根目录）
DEFAULT_SOURCE_FILES = [
    ".dev/fixture/大渡口1月巡查报告.docx",
    ".dev/fixture/大渡口2月巡查报告.docx",
    ".dev/fixture/大渡口3月巡查报告.docx",
    ".dev/fixture/大渡口4月巡查报告.docx",
    ".dev/fixture/大渡口5月巡查报告.docx",
]

# 默认查询提示词：明确要求用 document 工具读取工作区已上传的报告，
# 避免 leader 误选 sql_query（当前环境 BTManager 无 TO_Org_User 权限表）。
DEFAULT_PROMPT = (
    "请使用 document 工具（operation=r）逐一读取工作区中的 5 份月度巡查报告"
    "（大渡口1月巡查报告.docx、大渡口2月巡查报告.docx、大渡口3月巡查报告.docx、"
    "大渡口4月巡查报告.docx、大渡口5月巡查报告.docx），"
    "聚合提取其中“桥梓塘立交”1-5 月的数据并输出汇总。"
    "不要使用 sql_query。"
)

# 关键词校验：响应必须出现"桥梓塘"以及 1~5 月字样
BRIDGE_KEYWORD = "桥梓塘"
MONTH_KEYWORDS = ["1月", "2月", "3月", "4月", "5月"]


# ============================================================
# 工具函数
# ============================================================

def resolve_source_file(source_path: str) -> Path:
    project_root = get_project_root()
    full_path = project_root / source_path
    if not full_path.exists():
        raise FileNotFoundError(f"Source file not found: {full_path}")
    return full_path


async def upload_files_to_workspace(
    api: APIClient,
    workspace_id: str,
    source_files: List[str],
    verbose: bool = True,
) -> List[str]:
    """按顺序上传多份文件到工作区，返回已上传的文件名列表。"""
    uploaded_files: List[str] = []

    for source_path in source_files:
        try:
            file_path = resolve_source_file(source_path)
            if verbose:
                print_dim(f"Uploading: {file_path.name}")

            upload_result = await api.upload_workspace_file(workspace_id, file_path)
            if not upload_result.get("success", True):
                print_error(
                    f"Failed to upload {file_path.name}: "
                    f"{upload_result.get('message')}"
                )
                continue

            uploaded_files.append(file_path.name)
            if verbose:
                print_success(f"Uploaded: {file_path.name}")
        except FileNotFoundError as e:
            print_error(str(e))
        except Exception as e:
            print_error(f"Error uploading {source_path}: {e}")

    return uploaded_files


# ============================================================
# 主测试流程
# ============================================================

async def run_qiaozitang_monthly_query_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """
    跨5份月度巡查报告聚合查询桥梓塘立交数据。

    流程：
      1. 创建 session
      2. 上传 5 份 docx 到 workspace
      3. 发起查询对话
      4. 流式收集响应
      5. 等待对话完成
      6. 关键词校验：桥梓塘 + 1~5月 + document工具调用 + 响应非空
    """
    result = TestResult("qiaozitang_monthly_query", scenario_config)

    print_test_header(scenario_config.get(
        "description",
        "Qiaozitang Overpass Monthly Query Test (桥梓塘立交 1-5月聚合查询)",
    ))

    # ---------- Step 1: 创建 session ----------
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(
        title="Qiaozitang Monthly Query Test"
    )
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

    # ---------- Step 2: 上传 5 份报告 ----------
    print_step(2, "Uploading 5 monthly reports to workspace...", Colors.CYAN)
    source_files = scenario_config.get("source_files", DEFAULT_SOURCE_FILES)

    try:
        uploaded_files = await upload_files_to_workspace(
            api, workspace_id, source_files, verbose=verbose
        )
    except FileNotFoundError as e:
        print_error(str(e))
        result.errors.append(str(e))
        return result

    if len(uploaded_files) != len(source_files):
        msg = (
            f"Only uploaded {len(uploaded_files)}/{len(source_files)} files: "
            f"{uploaded_files}"
        )
        print_error(msg)
        result.errors.append(msg)
        return result

    print_success(f"All {len(uploaded_files)} reports uploaded: {uploaded_files}")

    # ---------- Step 3: 创建对话发起查询 ----------
    print_step(3, "Creating conversation with query prompt...", Colors.CYAN)
    prompt = scenario_config.get("prompt", DEFAULT_PROMPT)

    conv_result = await api.create_conversation(session_id, prompt)

    if not conv_result.get("success", True):
        print_error(
            f"Failed to create conversation: {conv_result.get('message')}"
        )
        result.errors.append(
            f"create_conversation: {conv_result.get('message')}"
        )
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")

    # ---------- Step 4: 等待 processing ----------
    print_step(4, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(
        api, conversation_id, "processing", timeout=15.0
    )

    # ---------- Step 5: 流式收集响应 ----------
    print_step(5, "Streaming response...", Colors.CYAN)
    extraction_timeout = scenario_config.get("extraction_timeout", 300.0)
    await collect_stream_output(
        api, conversation_id, result,
        verbose=verbose, timeout=extraction_timeout,
    )

    # ---------- Step 6: 等待完成 ----------
    print_step(6, "Waiting for conversation to complete...", Colors.CYAN)
    final_result = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=extraction_timeout,
    )
    result.response_text = extract_response_text(final_result)

    # ---------- Step 7: 关键词校验 ----------
    print_step(7, "Validating results...", Colors.CYAN)

    validation_passed = True

    # 校验 1: 响应非空
    if not result.response_text:
        print_error("Response text is empty")
        result.errors.append("No response text found")
        validation_passed = False
    else:
        print_success(f"Response length: {len(result.response_text)} chars")

    # 校验 2: 含 "桥梓塘"
    if result.response_text and BRIDGE_KEYWORD in result.response_text:
        print_success(f"Keyword '{BRIDGE_KEYWORD}' found in response")
    else:
        print_error(f"Keyword '{BRIDGE_KEYWORD}' NOT found in response")
        result.errors.append(f"missing keyword: {BRIDGE_KEYWORD}")
        validation_passed = False

    # 校验 3: 含 1~5 月关键词
    if result.response_text:
        found_months = [
            m for m in MONTH_KEYWORDS if m in result.response_text
        ]
        missing_months = [
            m for m in MONTH_KEYWORDS if m not in result.response_text
        ]
        if found_months:
            print_success(f"Month keywords found: {found_months}")
        if missing_months:
            print_warning(f"Month keywords missing: {missing_months}")
            # 至少应出现 4 个月份，否则视为聚合失败
            if len(found_months) < 4:
                print_error(
                    f"Too few month keywords ({len(found_months)}/5), "
                    f"aggregation likely failed"
                )
                result.errors.append(
                    f"missing month keywords: {missing_months}"
                )
                validation_passed = False

    # 校验 4: document 工具被调用
    if "document" in result.tool_calls:
        print_success("document tool was called")
    else:
        print_warning("document tool was NOT called (may have used other method)")

    # 汇总
    if validation_passed:
        print_success("All keyword validations passed")
    else:
        print_error("Some validations failed")

    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Qiaozitang Monthly Query Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

    return result
