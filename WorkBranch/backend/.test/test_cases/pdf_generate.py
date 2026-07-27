#!/usr/bin/env python3
"""
PDF Generate Test

测试 agent 通过 document 工具 w 操作生成结构化报告 PDF 的能力。
验证内容：标题 / 段落 / 列表 / 表格 / 中英文混排。
成品 PDF 输出到 .test/output/ 供人工打开核验。
"""

from pathlib import Path

from .base import (
    APIClient,
    TestResult,
    Colors,
    get_project_root,
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

# 默认输出目录（相对项目根）
DEFAULT_OUTPUT_DIR = ".test/output"

# Prompt 模板：强制 agent 调用 document w 生成 PDF 到指定绝对路径
DEFAULT_PROMPT_TEMPLATE = """请调用 document 工具（operation=w）生成一份结构化的桥梁定期检查报告 PDF 文件。

要求：
1. file_path 必须使用以下绝对路径（不要修改、不要使用其他路径）：
{pdf_path}

2. content 参数必须是 Markdown 格式，至少包含以下结构：
   - 一级标题：# 桥梁定期检查报告
   - 二级标题：## 工程概况、## 规范依据、## 检测结果、## BCI 评定、## 结论建议
   - 多段正文（含中英文混排，例如 "依据 JTG/T H21—2011《公路桥梁技术状况评定标准》，BCI (Bridge Condition Index) 反映桥梁整体技术状况"）
   - 至少一个无序列表（如病害清单）
   - 至少一个 Markdown 表格（如部件评分表，含 部件名称 / 评定等级 / 评分 等列）

3. 必须实际调用 document 工具生成 PDF 文件，不得仅用文字描述或回复。

4. 生成完成后用 chat 工具简要回复 PDF 文件路径。"""


# ============================================================
# 工具函数
# ============================================================

def _build_pdf_path() -> Path:
    """构造 PDF 输出绝对路径：{project_root}/.test/output/pdf_generate_<timestamp>.pdf

    并确保父目录存在。
    """
    project_root = get_project_root()
    output_dir = project_root / DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_name = f"pdf_generate_{get_timestamp()}.pdf"
    return output_dir / pdf_name


def _verify_pdf(pdf_path: Path) -> dict:
    """校验 PDF 文件，返回 {exists, size, page_count, error}"""
    info = {"exists": False, "size": 0, "page_count": 0, "error": None}
    if not pdf_path.exists():
        info["error"] = f"PDF 文件不存在: {pdf_path}"
        return info
    info["exists"] = True
    info["size"] = pdf_path.stat().st_size
    if info["size"] == 0:
        info["error"] = f"PDF 文件大小为 0: {pdf_path}"
        return info
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        info["page_count"] = len(reader.pages)
        if info["page_count"] < 1:
            info["error"] = f"PDF 页数为 0: {pdf_path}"
    except Exception as e:
        info["error"] = f"PDF 读取失败（pypdf）: {e}"
    return info


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
      1. 构造 PDF 输出绝对路径（.test/output/）
      2. 创建 session
      3. 发起对话（prompt 要求 agent 调 document w 生成 PDF）
      4. 流式收集响应
      5. 等待对话完成
      6. 校验：document 工具被调用 + PDF 文件存在 + size>0 + 页数>=1
      7. 醒目打印 PDF 绝对路径供人工核验
    """
    result = TestResult("pdf_generate", scenario_config)

    print_test_header(scenario_config.get(
        "description",
        "PDF Generate Test (agent 通过 document w 生成结构化报告 PDF)",
    ))

    # ---------- Step 1: 构造 PDF 输出绝对路径 ----------
    print_step(1, "Building PDF output path...", Colors.CYAN)
    pdf_path = _build_pdf_path()
    print_success(f"PDF target path: {pdf_path}")

    # ---------- Step 2: 创建 session ----------
    print_step(2, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="PDF Generate Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")

    # ---------- Step 3: 创建对话 ----------
    print_step(3, "Creating conversation with PDF generate prompt...", Colors.CYAN)
    prompt_template = scenario_config.get("prompt_template", DEFAULT_PROMPT_TEMPLATE)
    prompt = prompt_template.format(pdf_path=str(pdf_path))
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

    # 校验 2: PDF 文件存在 + size>0 + 页数>=1
    pdf_info = _verify_pdf(pdf_path)
    if pdf_info["exists"]:
        print_success(f"PDF exists: {pdf_path}")
        print_dim(f"  size={pdf_info['size']} bytes, pages={pdf_info['page_count']}")
    else:
        print_error(pdf_info["error"])

    if pdf_info["error"]:
        # 已存在的 error 不重复 append
        if pdf_info["error"] not in result.errors:
            result.errors.append(pdf_info["error"])
        validation_passed = False

    # 记录到 result 供 summary 查看
    result.workspace_files_checked = True
    result.prediction_report_found = pdf_info["exists"] and not pdf_info["error"]
    result.prediction_report_name = str(pdf_path) if pdf_info["exists"] else None
    result.prediction_report_size = pdf_info["size"]

    # 汇总
    if validation_passed:
        print_success("All validations passed")
        # 醒目打印 PDF 绝对路径供人工核验
        print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
        print(f"{Colors.GREEN}  PDF generated successfully{Colors.ENDC}")
        print(f"{Colors.GREEN}  Path:  {pdf_path}{Colors.ENDC}")
        print(f"{Colors.GREEN}  Size:  {pdf_info['size']} bytes{Colors.ENDC}")
        print(f"{Colors.GREEN}  Pages: {pdf_info['page_count']}{Colors.ENDC}")
        print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    else:
        print_error("Some validations failed")

    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  PDF Generate Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")

    return result
