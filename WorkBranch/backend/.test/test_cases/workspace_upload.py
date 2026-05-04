#!/usr/bin/env python3
"""
Workspace Upload Tests

测试工作区上传功能
"""

import asyncio
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
        for source_file in source_files:
            file_path = resolve_source_file(source_file)
            upload_result = await api.upload_workspace_file(workspace_id, file_path)
            if not upload_result.get("success", True):
                print_error(f"Failed to upload file: {upload_result.get('message')}")
                result.errors.append(f"upload_file: {upload_result.get('message')}")
                return result
            uploaded_files.append(file_path.name)
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
    
    if "read_document" in result.tool_calls:
        print_success("read_document tool was called")
    else:
        print_error("read_document tool was not called")
    
    if result.response_text:
        print_success(f"Response length: {len(result.response_text)} chars")
    else:
        print_error("No response text found")
    
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
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "Creating conversation with image upload...", Colors.CYAN)
    source_file = scenario_config.get("source_file", ".dev/table/测试图片.png")
    prompt = scenario_config.get("prompt", "请分析这张图片。")
    
    try:
        file_path = resolve_source_file(source_file)
        user_content_parts = [
            {"type": "text", "text": prompt},
            {"type": "image", "path": str(file_path)}
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
