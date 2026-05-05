#!/usr/bin/env python3
"""
Bridge Inspection Report Prediction Test

基于陈家阁大桥2018/2020/2022历史检测报告，预测2024年报告并与真实报告对比
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

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
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


BRIDGE_REPORT_ROOT = get_project_root() / ".dev" / "table" / "桥梁检测报告"

HISTORICAL_FILES = {
    "2018": BRIDGE_REPORT_ROOT / "2018" / "12陈家阁大桥定期检测2018.10.docx",
    "2020": BRIDGE_REPORT_ROOT / "2020" / "03 陈家阁大桥.doc",
    "2022": BRIDGE_REPORT_ROOT / "2022" / "09 陈家阁立交.doc",
}

GROUND_TRUTH_2024 = BRIDGE_REPORT_ROOT / "2024" / "003 陈家阁大桥+C级.doc"

PREDICTION_PROMPT = """工作区中已上传三份陈家阁大桥的历史检测报告文件：
- 12陈家阁大桥定期检测2018.10.docx（2018年）
- 03 陈家阁大桥.doc（2020年）
- 09 陈家阁立交.doc（2022年）

请按以下步骤完成任务：

**第一步：读取所有三份报告**
必须使用 read_document 工具依次读取上述三个文件（包括 .doc 和 .docx 格式），确保完整获取每份报告的内容。

**第二步：分析变化趋势**
分析该桥技术状况的变化趋势，包括：
- 各部件评分变化
- 主要病害发展情况

**第三步：预测2024年技术状况**
基于历史数据的变化规律，预测2024年的技术状况。

**第四步：生成预测报告**
生成一份完整的2024年桥梁检测报告，格式应与历史报告保持一致。请直接输出完整的预测报告内容，不要询问用户确认。"""


def resolve_source_file(source_path: Path) -> Path:
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    return source_path


async def upload_historical_reports(
    api: APIClient,
    workspace_id: str,
    verbose: bool = True,
) -> List[str]:
    uploaded_files = []
    backend_dir = str(Path(__file__).resolve().parents[2])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from service.agent_service.tools.document_tools import _convert_doc_to_docx
    
    cache_dir = Path(tempfile.gettempdir())
    
    for year, file_path in HISTORICAL_FILES.items():
        try:
            full_path = resolve_source_file(file_path)
            
            upload_file = full_path
            if full_path.suffix.lower() == ".doc":
                cache_name = f"bridge_{year}_{full_path.stem}.docx"
                cache_path = cache_dir / cache_name
                
                if cache_path.exists() and cache_path.stat().st_size > 1000:
                    upload_file = cache_path
                    if verbose:
                        print_success(f"Using cached {year}: {upload_file.name} ({cache_path.stat().st_size:,} bytes)")
                else:
                    if verbose:
                        print_dim(f"Pre-converting {year} .doc to .docx: {full_path.name}")
                    docx_path = _convert_doc_to_docx(str(full_path))
                    if docx_path:
                        try:
                            import shutil
                            shutil.copy2(docx_path, str(cache_path))
                            upload_file = cache_path
                            if verbose:
                                print_success(f"Converted & cached {year}: {upload_file.name}")
                        except Exception:
                            upload_file = Path(docx_path)
                            if verbose:
                                print_success(f"Converted {year}: {upload_file.name}")
                    else:
                        print_error(f"Failed to convert {year} .doc file, uploading original")
            
            if verbose:
                print_dim(f"Uploading {year} report: {upload_file.name}")
            
            upload_result = await api.upload_workspace_file(workspace_id, upload_file)
            if not upload_result.get("success", True):
                print_error(f"Failed to upload {year}: {upload_result.get('message')}")
                continue
            
            uploaded_files.append(upload_file.name)
            if verbose:
                print_success(f"Uploaded {year}: {upload_file.name}")
        except FileNotFoundError as e:
            print_error(str(e))
        except Exception as e:
            print_error(f"Error uploading {year}: {e}")
    
    return uploaded_files


async def read_ground_truth_report(ground_truth_path: Path) -> str:
    full_path = resolve_source_file(ground_truth_path)
    
    suffix = full_path.suffix.lower()
    backend_dir = str(Path(__file__).resolve().parents[2])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from service.agent_service.tools.document_tools import _docx_read, _convert_doc_to_docx
    
    if suffix == ".docx":
        result = _docx_read(str(full_path))
    elif suffix == ".doc":
        docx_path = _convert_doc_to_docx(str(full_path))
        if not docx_path:
            raise RuntimeError(f"Failed to convert .doc to .docx: {full_path}")
        result = _docx_read(docx_path)
    else:
        result = {"error": f"Unsupported format: {suffix}", "result": None}
    
    if result.get("error"):
        raise RuntimeError(f"Failed to read ground truth report: {result['error']}")
    
    return result["result"].get("content", "")


async def run_bridge_predict_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("bridge_predict", scenario_config)
    
    print_test_header(scenario_config.get(
        "description",
        "Bridge Inspection Report - Historical Data Prediction Test"
    ))
    
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    output_log = log_dir / f"bridge_predict_{timestamp}.md"
    
    print_step(1, "Validating historical report files...", Colors.CYAN)
    missing_files = []
    for year, path in HISTORICAL_FILES.items():
        if not path.exists():
            missing_files.append(f"{year}: {path.name}")
    
    if missing_files:
        error_msg = f"Missing historical files: {'; '.join(missing_files)}"
        print_error(error_msg)
        result.errors.append(error_msg)
        return result
    
    has_ground_truth = GROUND_TRUTH_2024.exists()
    if has_ground_truth:
        print_success(f"Ground truth 2024 found: {GROUND_TRUTH_2024.name}")
    else:
        print_dim(f"Ground truth 2024 not found (comparison will be skipped): {GROUND_TRUTH_2024.name}")
    
    print_step(2, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="Bridge Report Prediction - Chenjiage Bridge")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    print_dim(f"Workspace ID: {workspace_id}")
    
    print_step(3, "Uploading historical reports (2018/2020/2022)...", Colors.CYAN)
    uploaded = await upload_historical_reports(api, workspace_id, verbose=verbose)
    
    if len(uploaded) < 3:
        error_msg = f"Only {len(uploaded)}/3 historical reports uploaded"
        print_error(error_msg)
        result.errors.append(error_msg)
        return result
    
    print_success(f"All {len(uploaded)} historical reports uploaded")
    
    print_step(4, "Creating conversation with prediction prompt...", Colors.CYAN)
    prompt = scenario_config.get("prompt", PREDICTION_PROMPT)
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
    
    print_step(6, "Streaming prediction response...", Colors.CYAN)
    prediction_timeout = scenario_config.get("prediction_timeout", 600.0)
    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=prediction_timeout)
    
    max_followups = 3
    followup_count = 0
    while result.next_conversation_id and followup_count < max_followups:
        followup_count += 1
        if verbose:
            print(f"{Colors.YELLOW}[Follow-up #{followup_count}] Auto-approved plan detected, continuing with conversation: {result.next_conversation_id}{Colors.ENDC}")
        next_conv_id = result.next_conversation_id
        result.next_conversation_id = None
        conversation_id = next_conv_id
        result.conversation_id = conversation_id
        await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
        await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=prediction_timeout)
    
    if followup_count > 0:
        if verbose:
            print_success(f"Completed {followup_count} follow-up conversation(s)")
    
    print_step(7, "Waiting for conversation to complete...", Colors.CYAN)
    final_result = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=60.0
    )
    result.response_text = extract_response_text(final_result)
    
    print_step(8, "Reading ground truth 2024 report for comparison...", Colors.CYAN)
    ground_truth_content = ""
    if has_ground_truth:
        try:
            ground_truth_content = await read_ground_truth_report(GROUND_TRUTH_2024)
            print_success(f"Ground truth content length: {len(ground_truth_content)} chars")
        except Exception as e:
            print_error(f"Failed to read ground truth: {e}")
            result.errors.append(f"ground_truth_read: {e}")
    
    print_step(9, "Generating comparison report...", Colors.CYAN)
    
    with open(output_log, "w", encoding="utf-8") as f:
        f.write("# Bridge Report Prediction Test\n\n")
        f.write(f"- **Timestamp**: {timestamp}\n")
        f.write(f"- **Session ID**: {session_id}\n")
        f.write(f"- **Conversation ID**: {conversation_id}\n")
        f.write(f"- **Workspace ID**: {workspace_id}\n")
        f.write(f"- **Historical Files Used**:\n")
        for year, path in HISTORICAL_FILES.items():
            f.write(f"  - {year}: {path.name}\n")
        f.write(f"\n---\n\n")
        
        f.write("## AI Predicted 2024 Report\n\n")
        f.write("```\n")
        f.write(result.response_text or "(No response text captured)")
        f.write("\n```\n\n")
        
        f.write("---\n\n")
        
        if ground_truth_content:
            f.write("## Ground Truth 2024 Report\n\n")
            f.write("```\n")
            f.write(ground_truth_content[:10000])
            if len(ground_truth_content) > 10000:
                f.write(f"\n... (truncated, total {len(ground_truth_content)} chars)")
            f.write("\n```\n\n")
            
            f.write("---\n\n")
            f.write("## Comparison Notes\n\n")
            f.write("**Please manually compare the above two reports:**\n")
            f.write("- Technical condition rating consistency\n")
            f.write("- Major defect identification accuracy\n")
            f.write("- Trend analysis reasonableness\n")
            f.write("- Overall format and completeness\n\n")
        else:
            f.write("## Ground Truth 2024 Report\n\n")
            f.write("*Not available for comparison*\n\n")
        
        f.write(f"\n---\n\n")
        f.write("## Test Metadata\n\n")
        f.write(f"- **Tool Calls**: {result.tool_calls}\n")
        f.write(f"- **Event Count**: {result.event_count}\n")
        f.write(f"- **Detected Mode**: {result.detected_mode}\n")
        f.write(f"- **Errors**: {result.errors if result.errors else 'None'}\n")
    
    print_success(f"Comparison report saved to: {output_log}")
    
    print_step(10, "Validation summary...", Colors.CYAN)
    
    if result.response_text and len(result.response_text) > 100:
        print_success(f"Prediction generated: {len(result.response_text)} chars")
    elif result.response_text:
        print_error(f"Prediction too short: {len(result.response_text)} chars")
        result.errors.append("prediction_too_short")
    else:
        print_error("No prediction response received")
        result.errors.append("no_prediction_response")
    
    read_tools = ["read_document", "read_file"]
    used_read_tool = any(t in result.tool_calls for t in read_tools)
    if used_read_tool:
        print_success("Document reading tool was called")
    else:
        print_dim("Document reading tool may not have been called")
    
    write_tools = ["write_file"]
    used_write_tool = any(t in result.tool_calls for t in write_tools)
    if used_write_tool:
        print_success("File writing tool was called (report may be saved as file)")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Bridge Predict Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    print(f"{Colors.CYAN}Comparison log: {output_log}{Colors.ENDC}\n")
    
    return result
