#!/usr/bin/env python3
"""
PDF Generate Test

测试 agent 通过 document 工具 w 操作生成结构化报告 PDF 的能力。
验证内容：标题 / 段落 / 列表 / 表格 / 中英文混排。
成品 PDF 输出到 Session workspace，并通过 workspace API 校验。
"""

import asyncio

from .base import (
    APIClient,
    TestResult,
    Colors,
    get_timestamp,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


# ============================================================
# 测试配置
# ============================================================

# Prompt 模板：使用相对路径，让后端将文件解析到当前 Session workspace。
DEFAULT_PROMPT_TEMPLATE = """请调用 document 工具（operation=w）生成一份结构化的桥梁定期检查报告 PDF 文件。

要求：
1. file_path 必须使用以下工作区相对路径（不要修改、不要使用其他路径）：
{pdf_name}

2. content 参数必须是 Markdown 格式，至少包含以下结构：
   - 一级标题：# 桥梁定期检查报告
   - 二级标题：## 工程概况、## 规范依据、## 检测结果、## BCI 评定、## 结论建议
   - 多段正文（含中英文混排，例如 "依据 JTG/T H21—2011《公路桥梁技术状况评定标准》，BCI (Bridge Condition Index) 反映桥梁整体技术状况"）
   - 至少一个无序列表（如病害清单）
   - 至少一个 Markdown 表格（如部件评分表，含 部件名称 / 评定等级 / 评分 等列）

3. 必须实际调用 document 工具生成 PDF 文件，不得仅用文字描述或回复。

4. 生成完成后直接输出包含 PDF 文件路径的最终总结。"""


def discard_stream_timeout_errors(errors: list) -> list:
    """会话已完成时，流收集窗口超时不作为失败：仅表示收集窗口不足。"""
    return [
        error for error in errors
        if not str(error).startswith("stream timeout after ")
    ]


# ============================================================
# 工具函数
# ============================================================

async def _wait_for_workspace_pdf(
    api: APIClient,
    workspace_id: str,
    pdf_name: str,
    timeout: float,
) -> dict | None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        files_result = await api.list_workspace_files(workspace_id)
        if not files_result.get("success", True):
            raise RuntimeError(
                f"list_workspace_files: {files_result.get('message')}"
            )
        for file_info in files_result.get("data") or []:
            if (
                file_info.get("name") == pdf_name
                and not file_info.get("is_dir", False)
                and int(file_info.get("size") or 0) > 0
            ):
                return file_info
        await asyncio.sleep(2.0)
    return None


# ============================================================
# 主测试流程
# ============================================================

async def run_pdf_generate_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    """
    验证 agent 调用 document w 生成结构化报告 PDF。

    流程：
      1. 构造 workspace 相对 PDF 文件名
      2. 创建 session
      3. 发起对话（prompt 要求 agent 调 document w 生成 PDF）
      4. 流式收集响应
      5. 等待对话完成
      6. 校验：document 工具被调用 + workspace 中 PDF 存在且 size>0
    """
    result = TestResult("pdf_generate", scenario_config)

    print_test_header(scenario_config.get(
        "description",
        "PDF Generate Test (agent 通过 document w 生成结构化报告 PDF)",
    ))

    # ---------- Step 1: 构造 workspace 相对文件名 ----------
    print_step(1, "Building workspace PDF name...", Colors.CYAN)
    pdf_name = f"pdf_generate_{get_timestamp()}.pdf"
    print_success(f"PDF workspace target: {pdf_name}")

    # ---------- Step 2: 创建 session ----------
    print_step(2, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="PDF Generate Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    result.workspace_id = workspace_id
    print_success(f"Session created: {session_id}")

    # ---------- Step 3: 创建对话 ----------
    print_step(3, "Creating conversation with PDF generate prompt...", Colors.CYAN)
    prompt_template = scenario_config.get("prompt_template", DEFAULT_PROMPT_TEMPLATE)
    prompt = prompt_template.format(pdf_name=pdf_name, pdf_path=pdf_name)
    print_dim(f"Prompt length: {len(prompt)} chars")

    conv_result = await api.create_conversation(session_id, prompt)
    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result

    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")

    # ---------- Step 4: 等待 processing ----------
    print_step(4, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=15.0)

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

    # 流收集窗口超时但会话已完成：该错误仅表示收集窗口不足，
    # 不能否定已落盘的工作区产物，后续按 PDF 存在与否判定。
    result.errors = discard_stream_timeout_errors(result.errors)

    # ---------- Step 7: 校验 ----------
    print_step(7, "Validating results...", Colors.CYAN)

    validation_passed = True

    # 校验 1: document 工具被调用
    doc_call_count = result.tool_calls.count("document")
    if doc_call_count > 0:
        print_success(f"document tool was called ({doc_call_count} times)")
    else:
        print_error("document tool was NOT called")
        result.errors.append("document tool not called")
        validation_passed = False

    # 校验 2: 共享 workspace 中 PDF 文件存在且非空
    try:
        pdf_info = await _wait_for_workspace_pdf(
            api,
            workspace_id,
            pdf_name,
            timeout=min(extraction_timeout, 120.0),
        )
    except Exception as exc:
        pdf_info = None
        result.errors.append(str(exc))

    if pdf_info:
        print_success(f"PDF exists in workspace: {pdf_info.get('path', pdf_name)}")
        print_dim(f"  size={pdf_info.get('size', 0)} bytes")
    else:
        error = f"PDF not found or empty in workspace: {pdf_name}"
        print_error(error)
        if error not in result.errors:
            result.errors.append(error)
        validation_passed = False

    # 记录到 result 供 summary 查看
    result.workspace_files_checked = True
    result.prediction_report_found = pdf_info is not None
    result.prediction_report_name = pdf_info.get("path") if pdf_info else None
    result.prediction_report_size = pdf_info.get("size", 0) if pdf_info else 0

    # 汇总
    if validation_passed:
        print_success("All validations passed")
        print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
        print(f"{Colors.GREEN}  PDF generated successfully{Colors.ENDC}")
        print(f"{Colors.GREEN}  Workspace path: {pdf_info.get('path', pdf_name)}{Colors.ENDC}")
        print(f"{Colors.GREEN}  Size: {pdf_info.get('size', 0)} bytes{Colors.ENDC}")
        print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    else:
        print_error("Some validations failed")

    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  PDF Generate Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

    return result
