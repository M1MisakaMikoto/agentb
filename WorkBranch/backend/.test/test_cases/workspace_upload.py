#!/usr/bin/env python3
"""
Workspace Upload Tests

测试工作区上传功能
"""

import asyncio
import json
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
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


def resolve_source_file(source_path: str) -> Path:
    project_root = get_project_root()
    full_path = project_root / source_path
    if not full_path.exists():
        raise FileNotFoundError(f"Source file not found: {full_path}")
    return full_path


def parse_workspace_file_index(user_content: str) -> tuple[dict, str]:
    if not user_content.startswith("`"):
        raise ValueError("user_content does not start with a backtick file index")
    marker_end = user_content.find("`", 1)
    if marker_end < 0:
        raise ValueError("user_content file index is missing its closing backtick")
    return json.loads(user_content[1:marker_end]), user_content[marker_end + 1:].lstrip()


async def upload_files_to_workspace(
    api: APIClient,
    workspace_id: str,
    source_files: List[str],
    verbose: bool = True
) -> List[str]:
    uploaded_files = []
    
    for source_path in source_files:
        try:
            file_path = resolve_source_file(source_path)
            if verbose:
                print_dim(f"Uploading: {file_path.name}")
            
            upload_result = await api.upload_workspace_file(workspace_id, file_path)
            if not upload_result.get("success", True):
                print_error(f"Failed to upload {file_path.name}: {upload_result.get('message')}")
                continue
            
            uploaded_files.append(file_path.name)
            if verbose:
                print_success(f"Uploaded: {file_path.name}")
        except FileNotFoundError as e:
            print_error(str(e))
        except Exception as e:
            print_error(f"Error uploading {source_path}: {e}")
    
    return uploaded_files


async def run_workspace_upload_user_content_index_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True,
) -> TestResult:
    result = TestResult("workspace_upload_user_content_index", scenario_config)
    print_test_header("Workspace Upload - User Content Index Test")

    session_result = await api.create_session(title="Workspace File Index E2E")
    if not session_result.get("success", True):
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    session_data = session_result.get("data") or {}
    session_id = session_data["id"]
    workspace_id = session_data["workspace_id"]
    result.session_id = session_id
    result.workspace_id = workspace_id

    source_path = scenario_config.get("source_file", ".dev/table/我是测试知识文件.txt")
    file_path = resolve_source_file(source_path)
    upload_result = await api.upload_workspace_file(workspace_id, file_path)
    if not upload_result.get("success", True):
        result.errors.append(f"upload_file: {upload_result.get('message')}")
        return result
    saved_files = upload_result.get("data") or []
    if len(saved_files) != 1:
        result.errors.append(f"upload_file: unexpected saved file count {len(saved_files)}")
        return result
    saved_file = saved_files[0]
    expected_file = {
        "workspace_id": workspace_id,
        "name": file_path.name,
        "relative_path": saved_file["saved_as"],
        "size": saved_file["size"],
    }

    workspace_result = await api.list_workspace_files(workspace_id)
    workspace_files = workspace_result.get("data") or []
    matching_files = [
        item for item in workspace_files
        if not item["is_dir"] and item["path"] == expected_file["relative_path"]
    ]
    if len(matching_files) != 1 or matching_files[0]["size"] != expected_file["size"]:
        result.errors.append(f"workspace file mismatch: expected={expected_file}")
        return result

    prompt = scenario_config.get("prompt", "请确认已收到上传文件。")
    conversation_result = await api.create_conversation(session_id, prompt)
    if not conversation_result.get("success", True):
        result.errors.append(f"create_conversation: {conversation_result.get('message')}")
        return result
    conversation_id = (conversation_result.get("data") or {})["conversation_id"]
    result.conversation_id = conversation_id

    stream = api.stream_message(conversation_id)
    first_event_task = asyncio.create_task(stream.__anext__())
    actual_user_content = ""
    actual_parts = []
    try:
        await asyncio.wait_for(first_event_task, timeout=15.0)
        for _ in range(50):
            conversation = await api.get_conversation(conversation_id)
            conversation_data = conversation.get("data") or {}
            actual_user_content = conversation_data.get("user_content", "")
            actual_parts = conversation_data.get("user_content_parts", [])
            try:
                index, _ = parse_workspace_file_index(actual_user_content)
                if index.get("workspace_files"):
                    break
            except (ValueError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.1)
    finally:
        await api.cancel_conversation(conversation_id)
        await stream.aclose()

    expected_index = {"workspace_files": [expected_file]}
    try:
        actual_index, original_content = parse_workspace_file_index(actual_user_content)
    except (ValueError, json.JSONDecodeError) as exc:
        result.errors.append(f"workspace file index is not parseable: {exc}")
    else:
        if actual_index != expected_index:
            result.errors.append(
                f"workspace file index mismatch: expected={expected_index}, actual={actual_index}"
            )
        if original_content != prompt:
            result.errors.append(
                f"original user content mismatch: expected={prompt}, actual={original_content}"
            )
        expected_marker = f"`{json.dumps(expected_index, ensure_ascii=False, separators=(',', ':'))}`"
        if actual_parts[:1] != [{"type": "text", "text": expected_marker}]:
            result.errors.append(f"stored index marker mismatch: actual={actual_parts[:1]}")
    if not result.errors:
        print_success(f"Workspace file index verified: {expected_index}")
    return result


async def run_workspace_upload_extract_write_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True
) -> TestResult:
    result = TestResult("workspace_upload_extract_write", scenario_config)
    
    print_test_header(scenario_config.get("description", "Workspace Upload - Extract Write Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="Workspace Upload Extract Write Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "Creating conversation with file upload...", Colors.CYAN)
    source_file = scenario_config.get("source_file", ".dev/table/我是测试知识文件.txt")
    prompt = scenario_config.get("prompt", "请查看工作区中的文件并总结内容。")
    
    try:
        file_path = resolve_source_file(source_file)
        user_content_parts = [
            {"type": "text", "text": prompt},
            {"type": "file", "path": str(file_path)}
        ]
        conv_result = await api.create_conversation(session_id, prompt, user_content_parts=user_content_parts)
    except FileNotFoundError as e:
        print_error(str(e))
        result.errors.append(str(e))
        return result
    
    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    workspace_id = conv_result.get("data", {}).get("workspace_id")
    result.conversation_id = conversation_id
    result.workspace_id = workspace_id
    print_success(f"Conversation created: {conversation_id}")
    print_dim(f"Workspace ID: {workspace_id}")
    
    print_step(3, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
    
    print_step(4, "Streaming response...", Colors.CYAN)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    
    print_step(5, "Waiting for conversation to complete...", Colors.CYAN)
    final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=180.0)
    result.response_text = extract_response_text(final_result)
    
    print_step(6, "Validating results...", Colors.CYAN)
    
    if result.response_text:
        print_success(f"Response length: {len(result.response_text)} chars")
    else:
        print_error("No response text found")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Workspace Upload Extract Write Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result


async def run_workspace_upload_read_document_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True
) -> TestResult:
    result = TestResult("workspace_upload_read_document", scenario_config)
    
    print_test_header(scenario_config.get("description", "Workspace Upload - Read Document Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="Workspace Upload Read Document Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    print_success(f"Workspace ID: {workspace_id}")
    
    print_step(2, "Uploading file to workspace...", Colors.CYAN)
    source_files = scenario_config.get("source_files", [".dev/table/城市桥梁养护技术规程（标准文本）.pdf"])
    prompt = scenario_config.get("prompt", "请读取文档并总结内容。")
    
    try:
        uploaded_files = []
        expected_file_indexes = []
        for source_file in source_files:
            file_path = resolve_source_file(source_file)
            upload_result = await api.upload_workspace_file(workspace_id, file_path)
            if not upload_result.get("success", True):
                print_error(f"Failed to upload file: {upload_result.get('message')}")
                result.errors.append(f"upload_file: {upload_result.get('message')}")
                return result
            uploaded_files.append(file_path.name)
            saved_files = upload_result.get("data") or []
            if len(saved_files) != 1:
                result.errors.append(f"upload_file: unexpected saved file count {len(saved_files)}")
                return result
            saved_file = saved_files[0]
            expected_file_indexes.append({
                "workspace_id": workspace_id,
                "name": file_path.name,
                "relative_path": saved_file["saved_as"],
                "size": saved_file["size"],
            })
            print_success(f"Uploaded: {file_path.name}")
    except FileNotFoundError as e:
        print_error(str(e))
        result.errors.append(str(e))
        return result
    
    print_step(3, "Creating conversation with document prompt...", Colors.CYAN)
    conv_result = await api.create_conversation(session_id, prompt)
    
    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")
    
    print_step(4, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
    
    print_step(5, "Streaming response...", Colors.CYAN)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    
    print_step(6, "Waiting for conversation to complete...", Colors.CYAN)
    final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=300.0)
    result.response_text = extract_response_text(final_result)
    
    print_step(7, "Validating results...", Colors.CYAN)
    
    if "document" in result.tool_calls:
        print_success("document tool was called")
    else:
        print_error("document tool was not called")
    
    if result.response_text:
        print_success(f"Response length: {len(result.response_text)} chars")
    else:
        print_error("No response text found")

    conversation_data = final_result.get("data") or {}
    try:
        actual_index, _ = parse_workspace_file_index(conversation_data.get("user_content", ""))
        actual_file_indexes = actual_index.get("workspace_files")
    except (ValueError, json.JSONDecodeError) as exc:
        actual_file_indexes = None
        result.errors.append(f"workspace file index is not parseable: {exc}")
    if actual_file_indexes == expected_file_indexes:
        print_success("Workspace file indexes persisted in user_content")
    elif actual_file_indexes is not None:
        error = (
            "workspace_file indexes mismatch: "
            f"expected={expected_file_indexes}, actual={actual_file_indexes}"
        )
        print_error(error)
        result.errors.append(error)
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Workspace Upload Read Document Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result


async def run_workspace_upload_image_understanding_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True
) -> TestResult:
    result = TestResult("workspace_upload_image_understanding", scenario_config)
    
    print_test_header(scenario_config.get("description", "Workspace Upload - Image Understanding Test"))
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="Workspace Upload Image Understanding Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result

    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    result.workspace_id = workspace_id
    print_success(f"Session created: {session_id}")
    print_success(f"Workspace ID: {workspace_id}")

    print_step(2, "Uploading image to workspace...", Colors.CYAN)
    source_file = scenario_config.get("source_file", ".dev/table/测试图片.png")
    prompt = scenario_config.get("prompt", "请分析这张图片。")

    try:
        file_path = resolve_source_file(source_file)
        upload_result = await api.upload_workspace_file(workspace_id, file_path)
        if not upload_result.get("success", True):
            print_error(f"Failed to upload image: {upload_result.get('message')}")
            result.errors.append(f"upload_file: {upload_result.get('message')}")
            return result
        print_success(f"Uploaded: {file_path.name}")

        user_content_parts = [
            {"type": "text", "text": prompt},
            {"type": "image", "file_ref": file_path.name}
        ]
        conv_result = await api.create_conversation(session_id, prompt, user_content_parts=user_content_parts)
    except FileNotFoundError as e:
        print_error(str(e))
        result.errors.append(str(e))
        return result
    
    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    workspace_id = conv_result.get("data", {}).get("workspace_id")
    result.conversation_id = conversation_id
    result.workspace_id = workspace_id
    print_success(f"Conversation created: {conversation_id}")
    print_dim(f"Workspace ID: {workspace_id}")
    
    print_step(3, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
    
    print_step(4, "Streaming response...", Colors.CYAN)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    
    print_step(5, "Waiting for conversation to complete...", Colors.CYAN)
    final_result = await wait_for_conversation_state(api, conversation_id, "completed", timeout=180.0)
    result.response_text = extract_response_text(final_result)
    
    print_step(6, "Validating results...", Colors.CYAN)
    
    if result.response_text:
        print_success(f"Response length: {len(result.response_text)} chars")
        
        image_keywords = ["图片", "图表", "曲线", "算法"]
        found_keywords = [kw for kw in image_keywords if kw in result.response_text]
        if found_keywords:
            print_success(f"Image-related keywords found: {found_keywords}")
        else:
            print_error("No image-related keywords found in response")
    else:
        print_error("No response text found")
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  Workspace Upload Image Understanding Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result


async def run_workspace_upload_read_table_document_test(
    api: APIClient,
    scenario_config: dict,
    verbose: bool = True
) -> TestResult:
    """
    表格内容提取 E2E 测试

    验证 document 工具能否正确从 Word 文档中提取表格数据
    """
    result = TestResult("workspace_upload_read_table_document", scenario_config)
    
    print_test_header(scenario_config.get(
        "description",
        "Workspace Upload - Read Table Document Test (表格内容提取)"
    ))
    
    # 定义预期的表格数据（用于验证）
    expected_tables = {
        "table1": {
            "name": "桥梁基本信息",
            "headers": ["项目", "数值", "单位", "备注"],
            "expected_rows": 5,
            "expected_cols": 4,
            "keywords": ["朝阳寺立交桥", "1250.5", "2008"],
        },
        "table2": {
            "name": "病害统计明细",
            "headers": ["编号", "位置", "病害类型", "严重程度", "尺寸(cm)", "处置建议"],
            "expected_rows": 7,
            "expected_cols": 6,
            "keywords": ["D001", "纵向裂缝", "D005", "支座更换"],
        },
        "table3": {
            "name": "BCI评分记录",
            "headers": ["检测年份", "上部结构", "下部结构", "桥面系", "综合BCI"],
            "expected_rows": 6,
            "expected_cols": 5,
            "keywords": ["2019", "85.3", "78.5"],
        },
        "table4": {
            "name": "维修建议清单",
            "headers": ["优先级", "工作内容", "预估费用(万元)", "计划工期"],
            "expected_rows": 5,
            "expected_cols": 4,
            "keywords": ["紧急", "45.0", "2024Q1"],
        },
    }
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="Table Document Extraction Test")
    if not session_result.get("success", True):
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    workspace_id = session_result.get("data", {}).get("workspace_id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    print_success(f"Workspace ID: {workspace_id}")
    
    print_step(2, "Uploading table test document...", Colors.CYAN)
    source_file = scenario_config.get(
        "source_file",
        ".test/test_data/table_test_document.docx"
    )
    prompt = scenario_config.get(
        "prompt",
        (
            "请仔细读取文档中的所有表格数据，完整提取每个表格的内容。"
            "要求："
            "\n1. 提取全部4个表格的完整数据（包括表头和所有行）"
            "\n2. 保持表格的结构和格式"
            "\n3. 列出每个表格的名称、行列数、以及关键数据项"
            "\n4. 特别验证以下数据点："
            "   - 桥梁名称是否为'朝阳寺立交桥'"
            "   - 病害编号 D005 的处置建议是什么"
            "   - 2023 年的综合 BCI 分数是多少"
            "   - 紧急维修项目的预估费用"
        )
    )
    
    try:
        file_path = resolve_source_file(source_file)
        upload_result = await api.upload_workspace_file(workspace_id, file_path)
        if not upload_result.get("success", True):
            print_error(f"Failed to upload file: {upload_result.get('message')}")
            result.errors.append(f"upload_file: {upload_result.get('message')}")
            return result
        print_success(f"Uploaded: {file_path.name}")
    except FileNotFoundError as e:
        print_error(str(e))
        result.errors.append(str(e))
        return result
    
    print_step(3, "Creating conversation with table extraction prompt...", Colors.CYAN)
    conv_result = await api.create_conversation(session_id, prompt)
    
    if not conv_result.get("success", True):
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")
    
    print_step(4, "Waiting for conversation to be processing...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "processing", timeout=10.0)
    
    print_step(5, "Streaming response (table extraction)...", Colors.CYAN)
    await collect_stream_output(api, conversation_id, result, verbose=verbose)
    
    print_step(6, "Waiting for conversation to complete...", Colors.CYAN)
    final_result = await wait_for_conversation_state(
        api, conversation_id, "completed", timeout=300.0
    )
    result.response_text = extract_response_text(final_result)
    
    print_step(7, "Validating table extraction results...", Colors.CYAN)
    
    # 验证结果
    validation_passed = True
    
    if not result.response_text:
        print_error("No response text found")
        result.errors.append("No response text in response")
        validation_passed = False
    else:
        print_success(f"Response length: {len(result.response_text)} chars")
        
        # 检查 document 工具是否被调用
        if "document" in result.tool_calls:
            print_success("✓ document tool was called")
        else:
            print_warning("⚠ document tool was NOT called (may have used other method)")
        
        # 验证表格数据提取
        print(f"\n{Colors.CYAN}[Table Validation]{Colors.ENDC}")
        
        all_keywords_found = []
        missing_keywords = []
        
        for table_key, table_info in expected_tables.items():
            table_name = table_info["name"]
            keywords = table_info["keywords"]
            
            found_count = sum(1 for kw in keywords if kw in result.response_text)
            total_count = len(keywords)
            
            if found_count == total_count:
                print_success(
                    f"✓ {table_name}: "
                    f"{found_count}/{total_count} keywords found"
                )
                all_keywords_found.extend(keywords)
            else:
                found_kws = [kw for kw in keywords if kw in result.response_text]
                missing_kws = [kw for kw in keywords if kw not in result.response_text]
                
                print_error(
                    f"✗ {table_name}: "
                    f"Only {found_count}/{total_count} keywords found"
                )
                print_dim(f"  Found: {found_kws}")
                print_dim(f"  Missing: {missing_kws}")
                missing_keywords.extend(missing_kws)
                validation_passed = False
        
        # 检查 Markdown 表格格式
        markdown_table_indicators = ["| --- |", "| ---|", "--- |"]
        has_markdown_format = any(
            indicator in result.response_text
            for indicator in markdown_table_indicators
        )
        
        if has_markdown_format:
            print_success("✓ Response contains Markdown table format")
        else:
            print_warning("⚠ Response may not use standard Markdown table format")
        
        # 统计表格数量提及
        table_mentions = result.response_text.lower().count("表格") + \
                        result.response_text.lower().count("table")
        print_dim(f"Table mentions in response: {table_mentions}")
    
    # 设置验证结果
    result.table_extraction_passed = validation_passed
    result.missing_keywords = missing_keywords if 'missing_keywords' in dir() else []
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    if validation_passed:
        print(f"{Colors.GREEN}  ✓ Table Extraction Test PASSED{Colors.ENDC}")
    else:
        print(f"{Colors.RED}  ✗ Table Extraction Test FAILED{Colors.ENDC}")
        if result.missing_keywords:
            print(f"{Colors.RED}     Missing keywords: {result.missing_keywords}{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
