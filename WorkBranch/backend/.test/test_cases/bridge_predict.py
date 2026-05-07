#!/usr/bin/env python3
"""
Bridge Inspection Report Prediction Test

基于陈家阁大桥2018/2020/2022历史检测报告，预测2024年报告并与真实报告对比
"""

import asyncio
import os
import sys
import time
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

PREDICTION_PROMPT = """工作区中已上传三份陈家阁大桥的历史检测报告：
- 12陈家阁大桥定期检测2018.10.docx（2018年）
- 03 陈家阁大桥.doc（2020年）
- 09 陈家阁立交.doc（2022年）

## 任务步骤

1. **读取报告**：用 read_document 依次读取三份文件
2. **分析趋势**：分析各部件评分变化、病害发展规律
3. **预测2024**：基于历史数据预测技术状况
4. **输出报告**：用 document 工具写入 .docx 文件

## 输出要求

**必须使用 document 工具（operation=w）生成 Word 文档：**
- file_path: "2024年陈家阁大桥检测报告预测.docx"
- content: 完整的 Markdown 格式报告
- metadata: {"title":"2024年陈家阁大桥检测报告","author":"AI预测"}

## 报告结构（必须包含以下章节）

```
{{cover:title}} 检 测   报   告
{{cover:date}} 2024年11月05日
{{cover:company}} 中公诚科（吉林）工程咨询有限公司

# 大渡口区2024年陈家阁立交桥结构定期检测报告

## 一、工程概况
（桥梁位置、类型、跨径、桥宽等）

## 二、规范依据
（CJJ 99-2017, CJJ/T 233-2015 等）

## 三、检测目的

## 四、检测内容及方法
（含检测内容表格）

## 五、主要测试仪器
（仪器设备表格）

## 六、检测结果
### 6.1 表观检查结果（桥面系/上部/下部）
### 6.2 材质状况检测结果
### 6.3 桥梁技术状况等级评定（含 BCI 计算表格）

## 七、与上次检查结果对比

## 八、典型病害分析

## 九、安全性评估

## 十、检测结论及处治建议
```

## 格式要求

- 使用标准 Markdown 表格语法
- 标题层级：# 大章节 → ## 小节 → ### 子节
- 数据具体：BCI数值、评分、扣分值需基于历史数据推算
- 直接执行，不要询问确认！"""


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
    while (result.next_conversation_id or result.detected_mode == "PLAN") and followup_count < max_followups:
        followup_count += 1
        if result.next_conversation_id:
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] Auto-approved plan detected, continuing with conversation: {result.next_conversation_id}{Colors.ENDC}")
            next_conv_id = result.next_conversation_id
            result.next_conversation_id = None
            conversation_id = next_conv_id
            result.conversation_id = conversation_id
            await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
            await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=prediction_timeout)
        elif result.detected_mode == "PLAN":
            if verbose:
                print(f"{Colors.YELLOW}[Follow-up #{followup_count}] PLAN mode detected, sending approval...{Colors.ENDC}")
            try:
                approve_result = await api.approve_plan(result.workspace_id, approved=True)
                if verbose:
                    print(f"{Colors.DIM}[Follow-up #{followup_count}] Plan approval result: {approve_result.get('message', 'ok')}{Colors.ENDC}")
            except Exception as e:
                if verbose:
                    print(f"{Colors.DIM}[Follow-up #{followup_count}] Approve plan failed: {e}, trying create_conversation...{Colors.ENDC}")
            conv_result = await api.create_conversation(session_id, "可以")
            if conv_result.get("success", True):
                next_conv_id = conv_result.get("data", {}).get("conversation_id")
                if next_conv_id:
                    conversation_id = next_conv_id
                    result.conversation_id = conversation_id
                    result.detected_mode = None
                    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
                    await collect_stream_output(api, conversation_id, result, verbose=verbose, timeout=prediction_timeout)
                else:
                    break
            else:
                print_error(f"Failed to create follow-up: {conv_result.get('message')}")
                break
    
    if followup_count > 0:
        if verbose:
            print_success(f"Completed {followup_count} follow-up conversation(s)")
    
    # [方案A] 增强错误恢复: 检查流输出状态并尝试恢复
    print_step(6.5, "Checking stream output status and recovery...", Colors.CYAN)
    
    conv_check = await api.get_conversation(conversation_id)
    current_state = conv_check.get("data", {}).get("state")
    
    if current_state == "running" and not result.response_text:
        if verbose:
            print(f"{Colors.YELLOW}[Recovery] Stream interrupted but conversation still running (state={current_state}){Colors.ENDC}")
            print(f"{Colors.DIM}[Recovery] Event count: {result.event_count}, Tool calls: {result.tool_calls}{Colors.ENDC}")
        
        # 额外等待 AI 完成（最多3分钟）
        extra_wait_timeout = min(180.0, prediction_timeout / 2)
        if verbose:
            print(f"{Colors.DIM}[Recovery] Waiting up to {extra_wait_timeout:.0f}s for AI to complete...{Colors.ENDC}")
        
        recovery_result = await wait_for_conversation_state(
            api, conversation_id, "completed", timeout=extra_wait_timeout
        )
        
        recovery_state = recovery_result.get("data", {}).get("state")
        result.response_text = extract_response_text(recovery_result)
        
        if result.response_text:
            if verbose:
                print_success(f"[Recovery] Successfully captured response after recovery ({len(result.response_text)} chars)")
        else:
            if verbose:
                print_warning(f"[Recovery] No response captured, final state: {recovery_state}")
    elif current_state == "completed":
        if verbose:
            print_success("[Recovery] Conversation already completed")
        result.response_text = extract_response_text(conv_check)
    else:
        if verbose:
            print(f"{Colors.DIM}[Recovery] Current state: {current_state}{Colors.ENDC}")
    
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
    
    # [方案B] 工作区轮询: 验证预测报告是否已生成
    print_step(8.5, "Checking workspace for generated prediction report...", Colors.CYAN)
    
    prediction_report_found = False
    prediction_report_info = None
    
    max_workspace_wait = 120  # 最多额外等待2分钟
    workspace_poll_start = time.time()
    
    while time.time() - workspace_poll_start < max_workspace_wait:
        try:
            files_result = await api.list_workspace_files(workspace_id)
            files_data = files_result.get("data") or []
            
            if verbose:
                print(f"{Colors.DIM}[Workspace Check] Total files: {len(files_data)}{Colors.ENDC}")
            
            # 查找预测报告 (包含2024且为.docx格式)
            pred_files = [
                f for f in files_data 
                if ("2024" in f.get("name", "") or "预测" in f.get("name", "") or "预测" in f.get("name", ""))
                and f.get("name", "").endswith(".docx")
            ]
            
            # 也检查任何新生成的 .docx 文件（排除已上传的历史文件）
            original_filenames = {path.name for path in HISTORICAL_FILES.values()}
            new_docx_files = [
                f for f in files_data 
                if f.get("name", "").endswith(".docx")
                and f.get("name", "") not in original_filenames
                and not f["name"].startswith("bridge_")  # 排除缓存的转换文件
            ]
            
            if pred_files:
                prediction_report_found = True
                prediction_report_info = pred_files[0]
                if verbose:
                    print_success(f"[Workspace] Found prediction report: {prediction_report_info['name']} ({prediction_report_info.get('size', 0):,} bytes)")
                break
            elif new_docx_files:
                prediction_report_found = True
                prediction_report_info = new_docx_files[0]
                if verbose:
                    print_success(f"[Workspace] Found new docx file: {prediction_report_info['name']} ({prediction_report_info.get('size', 0):,} bytes)")
                break
            else:
                if verbose and int(time.time() - workspace_poll_start) % 30 == 0:
                    elapsed = int(time.time() - workspace_poll_start)
                    print(f"{Colors.DIM}[Workspace] Waiting... ({elapsed}s elapsed){Colors.ENDC}")
                
                await asyncio.sleep(10)
                
        except Exception as e:
            if verbose:
                print_warning(f"[Workspace] Failed to list files: {e}")
            await asyncio.sleep(10)
    
    if not prediction_report_found:
        if verbose:
            print_warning("[Workspace] No prediction report found after extended wait")
        result.errors.append("workspace_check: No prediction report (.docx) found in workspace")
    
    # 记录工作区状态到结果
    result.workspace_files_checked = True
    result.prediction_report_found = prediction_report_found
    if prediction_report_info:
        result.prediction_report_name = prediction_report_info.get("name")
        result.prediction_report_size = prediction_report_info.get("size", 0)
    
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
        
        # [方案B] 添加工作区检查结果到日志
        if result.workspace_files_checked:
            f.write(f"\n---\n\n")
            f.write("## Workspace Check Results\n\n")
            f.write(f"- **Prediction Report Found**: {'✅ Yes' if result.prediction_report_found else '❌ No'}\n")
            if result.prediction_report_found:
                f.write(f"- **Report Name**: {result.prediction_report_name}\n")
                f.write(f"- **Report Size**: {result.prediction_report_size:,} bytes ({result.prediction_report_size/1024:.1f} KB)\n")
    
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
    
    # [方案B] 验证工作区检查结果
    if result.workspace_files_checked:
        if result.prediction_report_found:
            print_success(f"Prediction report found in workspace: {result.prediction_report_name} ({result.prediction_report_size/1024:.1f} KB)")
        else:
            print_warning("No prediction report found in workspace (AI may have output text only)")
    
    read_tools = ["read_document", "read_file"]
    used_read_tool = any(t in result.tool_calls for t in read_tools)
    if used_read_tool:
        print_success("Document reading tool was called")
    else:
        print_dim("Document reading tool may not have been called")
    
    write_tools = ["write_file", "document"]
    used_write_tool = any(t in result.tool_calls for t in write_tools)
    if used_write_tool:
        print_success("File writing tool was called (report may be saved as file)")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Bridge Predict Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    print(f"{Colors.CYAN}Comparison log: {output_log}{Colors.ENDC}\n")
    
    return result
