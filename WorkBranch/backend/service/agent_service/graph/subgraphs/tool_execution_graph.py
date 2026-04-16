from typing import TypedDict, List, Optional, Literal, Callable
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from langgraph.graph import StateGraph, END
import os
import shutil

from ...state import ToolExecutionState, ToolCall
from ...tools import ALL_TOOLS, FILE_TOOLS, EXPLORE_TOOLS, SUBAGENT_TOOLS, WORKSPACE_TOOLS
from service.session_service.canonical import SegmentType
from core.logging import console
import fnmatch


FILE_TOOLS = {"read_file", "write_file", "delete_file", "list_dir", "create_dir"}
EXPLORE_TOOLS = {"explore_code", "explore_internet"}
SUBAGENT_TOOLS = {"call_explore_agent", "call_review_agent"}

# 配置哪些工具使用特殊处理（不发送tool_call/tool_res，而是使用专门的段类型）
SPECIAL_TOOLS = {
    "thinking": {
        "start_type": SegmentType.THINKING_START,
        "delta_type": SegmentType.THINKING_DELTA,
        "end_type": SegmentType.THINKING_END,
        "content_field": "thinking_content"
    },
    "chat": {
        "start_type": SegmentType.CHAT_START,
        "delta_type": SegmentType.CHAT_DELTA,
        "end_type": SegmentType.CHAT_END,
        "content_field": "chat_content"
    }
}


def _summarize_text(value: str, limit: int = 160) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."



def _write_tool_event(
    conversation_id: Optional[str],
    tool_name: str,
    status: Literal["started", "completed", "failed"],
    *,
    task_description: str = "",
    result: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    if not conversation_id:
        return

    payload = {
        "tool_name": tool_name,
        "status": status,
    }
    summary = ""
    if status == "started":
        summary = _summarize_text(task_description or f"started {tool_name}")
    elif status == "completed":
        summary = _summarize_text(result or f"completed {tool_name}")
    elif status == "failed":
        summary = _summarize_text(error or f"failed {tool_name}")

    if summary:
        payload["summary"] = summary
    if error:
        payload["error"] = _summarize_text(error)

    from singleton import get_logging_runtime

    get_logging_runtime().write_conversation_content(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id,
            "type": "tool_event",
            "payload": payload,
        }
    )



def get_allowed_tools(agent_type: str, settings_service=None) -> List[str]:
    """
    根据 agent 类型获取允许使用的工具列表
    
    Args:
        agent_type: Agent 类型 (coder, reviewer, explorer, admin)
        settings_service: 设置服务实例
        
    Returns:
        允许使用的工具名称列表
    """
    if settings_service is None:
        from service.settings_service.settings_service import SettingsService
        settings_service = SettingsService()
    
    try:
        permissions = settings_service.get("tool_permissions")
        if agent_type in permissions:
            return permissions[agent_type].get("allowed", [])
    except KeyError:
        pass
    
    default_permissions = {
        "build_agent": ["read_file", "write_file", "delete_file", "list_dir", "create_dir", "explore_code", "explore_internet", "thinking", "chat", "call_explore_agent", "call_review_agent", "list_workspace_files", "get_workspace_info", "search_files"],
        "plan_agent": ["read_file", "list_dir", "explore_code", "thinking", "chat", "call_explore_agent", "call_review_agent"],
        "review_agent": ["read_file", "list_dir", "explore_code", "thinking", "chat"],
        "explore_agent": ["read_file", "list_dir", "thinking", "chat", "explore_internet", "list_workspace_files", "get_workspace_info", "search_files"],
        "admin_agent": ["read_file", "write_file", "delete_file", "list_dir", "create_dir", "explore_code", "explore_internet", "thinking", "chat", "call_explore_agent", "call_review_agent", "list_workspace_files", "get_workspace_info", "search_files"]
    }
    return default_permissions.get(agent_type, default_permissions["build_agent"])


def filter_tools_by_agent_type(agent_type: str, settings_service=None) -> List[dict]:
    """
    根据 agent 类型过滤工具列表
    
    Args:
        agent_type: Agent 类型
        settings_service: 设置服务实例
        
    Returns:
        过滤后的工具定义列表
    """
    allowed_tools = get_allowed_tools(agent_type, settings_service)
    return [ALL_TOOLS[name] for name in allowed_tools if name in ALL_TOOLS]


def generate_tool_prompt(agent_type: str, settings_service=None) -> str:
    """
    根据 agent 类型生成工具说明 prompt
    
    Args:
        agent_type: Agent 类型
        settings_service: 设置服务实例
        
    Returns:
        工具说明文本
    """
    tools = filter_tools_by_agent_type(agent_type, settings_service)
    lines = ["可用的工具包括："]
    for tool in tools:
        params_str = f", 参数: {tool['params']}" if tool['params'] else ""
        lines.append(f"- {tool['name']}: {tool['description']}{params_str}")
    result = "\n".join(lines)
    print(f"[Tool Prompt] agent_type={agent_type}, tools={[t['name'] for t in tools]}")
    return result


def is_tool_allowed(tool_name: str, agent_type: str, settings_service=None) -> bool:
    """
    检查指定工具是否对当前 agent 类型可用
    
    Args:
        tool_name: 工具名称
        agent_type: Agent 类型
        settings_service: 设置服务实例
        
    Returns:
        是否允许使用
    """
    allowed_tools = get_allowed_tools(agent_type, settings_service)
    return tool_name in allowed_tools

THINK_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前正在执行一个任务计划中的某个步骤。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请针对当前任务进行思考：
1. 分析任务目标
2. 结合之前的执行结果（如果有）
3. 给出你的思考过程和结论

请简洁清晰地回答，不要过于冗长。"""

CHAT_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前需要向用户输出回复。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请直接向用户输出回复内容：
- 语言简洁清晰
- 直接回答用户问题
- 不要输出思考过程，只输出最终回复
- 使用友好、专业的语气"""


def check_permission(state: ToolExecutionState, workspace_service=None, settings_service=None) -> dict:
    """权限检查"""
    console.section("ToolExec 权限检查")
    
    tool_name = state["tool_name"]
    workspace_id = state["workspace_id"]
    tool_args = state["tool_args"]
    agent_type = state.get("agent_type", "build_agent")
    
    console.info(f"工具: {tool_name}")
    console.info(f"工作区: {workspace_id}")
    console.info(f"Agent 类型: {agent_type}")
    
    if not is_tool_allowed(tool_name, agent_type, settings_service):
        error_msg = f"工具 '{tool_name}' 不允许被 '{agent_type}' 类型的 Agent 使用"
        console.error(f"工具权限拒绝: {error_msg}")
        return {"permission": "deny", "error": error_msg}
    
    console.success("工具权限检查通过")

    if tool_name in FILE_TOOLS and workspace_service:
        path_key = "path" if "path" in tool_args else "file_path"
        target_path = tool_args.get(path_key) or tool_args.get("directory")
        
        if target_path:
            allowed, resolved_or_error = workspace_service.resolve_path(workspace_id, target_path)
            if not allowed:
                console.error(f"路径验证失败: {resolved_or_error}")
                return {"permission": "deny", "error": resolved_or_error}
            console.success(f"路径验证通过: {resolved_or_error}")
    
    dangerous_tools = ["delete_file", "execute_command", "modify_system"]
    
    if tool_name in dangerous_tools:
        console.warning("危险工具，需要用户确认")
        return {"permission": "ask"}
    
    console.success("权限检查通过")
    return {"permission": "allow"}


def route_by_permission(state: ToolExecutionState) -> str:
    """根据权限路由"""
    return state["permission"]


def ask_user(state: ToolExecutionState) -> dict:
    """询问用户（模拟）"""
    console.info("询问用户确认...")
    console.info(f"是否允许执行 {state['tool_name']}?")
    console.success("模拟用户同意")
    return {"permission": "allow"}


def deny_execution(state: ToolExecutionState, message_context: dict = None) -> dict:
    """拒绝执行"""
    console.error("执行被拒绝")
    error = state.get("error", "Permission denied")
    tool_name = state["tool_name"]
    tool_args = state["tool_args"]
    task_description = state.get("task_description", "")
    
    if message_context:
        send_message = message_context.get("send_message")
        if send_message and tool_name not in SPECIAL_TOOLS:
            send_message("", SegmentType.TOOL_CALL, {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "task_description": task_description
            })
        
        if send_message:
            send_message("", SegmentType.TOOL_RES, {
                "tool_name": tool_name,
                "result": None,
                "error": error,
                "success": False,
                "denied": True
            })
    
    return {"error": error, "result": None}


def execute_tool(state: ToolExecutionState, workspace_service=None, llm_service=None, token_callback: Optional[Callable[[str], None]] = None, message_context: dict = None) -> dict:
    """执行工具"""
    console.section("ToolExec 执行工具")
    
    if message_context:
        cancel_check = message_context.get("cancel_check")
        if cancel_check:
            cancel_check()
    
    tool_name = state["tool_name"]
    tool_args = state["tool_args"].copy()
    workspace_id = state["workspace_id"]
    task_description = state.get("task_description", "")
    previous_results = state.get("previous_results", [])
    conversation_id = None
    if message_context:
        conversation_id = message_context.get("conversation_id")

    console.info(f"工具: {tool_name}")
    console.info(f"参数: {tool_args}")
    console.info(f"任务描述: {task_description}")
    console.info(f"之前结果数量: {len(previous_results)}")
    
    if message_context:
        send_message = message_context.get("send_message")
        if send_message and tool_name not in SPECIAL_TOOLS:
            send_message("", SegmentType.TOOL_CALL, {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "task_description": task_description
            })

    _write_tool_event(
        conversation_id,
        tool_name,
        "started",
        task_description=task_description,
    )

    if tool_name in FILE_TOOLS and workspace_service:
        path_key = "path" if "path" in tool_args else "file_path"
        target_path = tool_args.get(path_key) or tool_args.get("directory")
        
        if target_path:
            allowed, resolved_path = workspace_service.resolve_path(workspace_id, target_path)
            if allowed:
                if "path" in tool_args:
                    tool_args["path"] = resolved_path
                elif "file_path" in tool_args:
                    tool_args["file_path"] = resolved_path
                elif "directory" in tool_args:
                    tool_args["directory"] = resolved_path
                console.info(f"路径已解析: {resolved_path}")
    
    if tool_name in EXPLORE_TOOLS and workspace_service:
        workspace_root = workspace_service.get_workspace_dir(workspace_id)
        if workspace_root:
            tool_args["workspace_root"] = workspace_root
            console.info(f"工作区根目录: {workspace_root}")
    
    if tool_name in SPECIAL_TOOLS:
        tool_result = _execute_special_tool(
            tool_name, tool_args, task_description, llm_service, message_context, token_callback
        )
        if tool_result.get("error") is None:
            _write_tool_event(
                conversation_id,
                tool_name,
                "completed",
                result=tool_result.get("result") or "",
            )
        else:
            _write_tool_event(
                conversation_id,
                tool_name,
                "failed",
                error=str(tool_result.get("error")),
            )
        return tool_result
    
    if tool_name == "read_file":
        tool_result = _execute_read_file(tool_args)
    elif tool_name == "write_file":
        tool_result = _execute_write_file(tool_args)
    elif tool_name == "delete_file":
        tool_result = _execute_delete_file(tool_args)
    elif tool_name == "list_dir":
        tool_result = _execute_list_dir(tool_args)
    elif tool_name == "create_dir":
        tool_result = _execute_create_dir(tool_args)
    elif tool_name == "explore_code":
        tool_result = _execute_explore_code(tool_args)
    elif tool_name == "explore_internet":
        tool_result = _execute_explore_internet(tool_args)
    elif tool_name == "call_explore_agent":
        tool_result = _execute_call_explore_agent(tool_args, llm_service, token_callback, message_context)
    elif tool_name == "call_review_agent":
        tool_result = _execute_call_review_agent(tool_args, llm_service, token_callback, message_context)
    elif tool_name in WORKSPACE_TOOLS:
        tool_result = _execute_workspace_tool(tool_name, tool_args, workspace_id, workspace_service)
    else:
        tool_result = {"result": f"工具 {tool_name} 执行成功", "error": None}
        console.success(f"结果: {tool_result['result']}")
    
    if message_context:
        send_message = message_context.get("send_message")
        if send_message:
            result_content = tool_result.get("result")
            if result_content is None:
                result_preview = ""
            else:
                result_preview = str(result_content)
                if len(result_preview) > 500:
                    result_preview = result_preview[:500] + "..."
            send_message("", SegmentType.TOOL_RES, {
                "tool_name": tool_name,
                "result": result_preview,
                "error": tool_result.get("error"),
                "success": tool_result.get("error") is None
            })

    if tool_result.get("error") is None:
        _write_tool_event(
            conversation_id,
            tool_name,
            "completed",
            result=tool_result.get("result") or "",
        )
    else:
        _write_tool_event(
            conversation_id,
            tool_name,
            "failed",
            error=str(tool_result.get("error")),
        )

    return tool_result


def _execute_special_tool(
    tool_name: str,
    tool_args: dict,
    task_description: str,
    llm_service,
    message_context: dict,
    token_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """处理特殊工具的执行逻辑"""
    if tool_name not in SPECIAL_TOOLS:
        return {"result": f"未知特殊工具: {tool_name}", "error": f"Unknown special tool: {tool_name}"}

    config = SPECIAL_TOOLS[tool_name]
    send_message = message_context.get("send_message") if message_context else None

    if tool_name == "thinking":
        return _execute_thinking_tool(tool_name, tool_args, task_description, llm_service, message_context, config)
    
    if tool_name == "chat":
        return _execute_chat_tool(tool_name, tool_args, task_description, llm_service, message_context, config)

    return {"result": f"特殊工具 {tool_name} 未实现", "error": f"Special tool {tool_name} not implemented"}


def _execute_thinking_tool(
    tool_name: str,
    tool_args: dict,
    task_description: str,
    llm_service,
    message_context: dict,
    config: dict,
) -> dict:
    """处理thinking工具的特殊逻辑"""
    previous_results = tool_args.get("previous_results", [])
    
    if not llm_service:
        result = f"思考任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    console.info("调用 LLM 进行思考...")
    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", config["start_type"], {
            "task_description": task_description,
            "is_start": True
        })

    try:
        context_parts = [f"当前任务: {task_description}"]

        if previous_results:
            context_parts.append("\n--- 之前任务的执行结果 ---")
            for i, prev_result in enumerate(previous_results, 1):
                truncated = prev_result[:500] + "..." if len(prev_result) > 500 else prev_result
                context_parts.append(f"任务{i}结果:\n{truncated}")
            context_parts.append("---\n")

        context_parts.append("请思考并执行当前任务。")
        prompt = "\n".join(context_parts)
        messages = [{"role": "user", "content": prompt}]

        def thinking_token_callback(token: str):
            if send_message:
                send_message(token, config["delta_type"], {
                    "task_description": task_description,
                    "is_delta": True
                })

        result = ""
        for chunk in llm_service.chat_stream(messages, THINK_SYSTEM_PROMPT, thinking_token_callback):
            result += chunk

        console.success("思考完成")

        if send_message:
            send_message("", config["end_type"], {
                "task_description": task_description,
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", config["end_type"], {
                "task_description": task_description,
                "is_end": True,
                "error": str(e)
            })
        return {"result": f"思考失败: {e}", "error": str(e)}


def _execute_chat_tool(
    tool_name: str,
    tool_args: dict,
    task_description: str,
    llm_service,
    message_context: dict,
    config: dict,
) -> dict:
    """处理chat工具的特殊逻辑"""
    previous_results = tool_args.get("previous_results", [])
    
    if not llm_service:
        result = f"回复任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    console.info("调用 LLM 进行对话回复...")
    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", config["start_type"], {
            "task_description": task_description,
            "is_start": True
        })

    try:
        context_parts = [f"当前任务: {task_description}"]

        if previous_results:
            context_parts.append("\n--- 之前任务的执行结果 ---")
            for i, prev_result in enumerate(previous_results, 1):
                truncated = prev_result[:500] + "..." if len(prev_result) > 500 else prev_result
                context_parts.append(f"任务{i}结果:\n{truncated}")
            context_parts.append("---\n")

        context_parts.append("请向用户输出回复。")
        prompt = "\n".join(context_parts)
        messages = [{"role": "user", "content": prompt}]

        def chat_token_callback(token: str):
            if send_message:
                send_message(token, config["delta_type"], {
                    "task_description": task_description,
                    "is_delta": True
                })

        result = ""
        for chunk in llm_service.chat_stream(messages, CHAT_SYSTEM_PROMPT, chat_token_callback):
            result += chunk

        console.success("对话回复完成")

        if send_message:
            send_message("", config["end_type"], {
                "task_description": task_description,
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", config["end_type"], {
                "task_description": task_description,
                "is_end": True,
                "error": str(e)
            })
        return {"result": f"对话回复失败: {e}", "error": str(e)}


def _execute_read_file(tool_args: dict) -> dict:
    """执行 read_file 工具"""
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    encoding = tool_args.get("encoding", "utf-8")
    start_line = tool_args.get("start_line", 1)
    end_line = tool_args.get("end_line")
    
    console.info(f"read_file: {file_path}")
    
    try:
        if not os.path.exists(file_path):
            return {"result": None, "error": f"文件不存在: {file_path}"}
        
        if not os.path.isfile(file_path):
            return {"result": None, "error": f"路径不是文件: {file_path}"}
        
        with open(file_path, "r", encoding=encoding) as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        start_idx = max(0, start_line - 1)
        end_idx = end_line if end_line else total_lines
        
        selected_lines = lines[start_idx:end_idx]
        
        result_lines = []
        for i, line in enumerate(selected_lines, start=start_idx + 1):
            result_lines.append(f"{i:6d}\t{line.rstrip()}")
        
        content = "\n".join(result_lines)
        if end_line is None or end_line >= total_lines:
            summary = f"文件共 {total_lines} 行，已读取全部内容"
        else:
            summary = f"文件共 {total_lines} 行，已读取第 {start_line}-{end_line} 行"
        
        console.success(f"read_file 成功: {summary}")
        return {"result": f"{summary}\n\n{content}", "error": None}
    
    except UnicodeDecodeError:
        return {"result": None, "error": f"文件编码错误，无法用 {encoding} 解码"}
    except Exception as e:
        console.error(f"read_file 失败: {e}")
        return {"result": None, "error": f"读取文件失败: {str(e)}"}


def _execute_write_file(tool_args: dict) -> dict:
    """执行 write_file 工具"""
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    content = tool_args.get("content")
    if content is None:
        return {"result": None, "error": "缺少 content 参数"}
    
    mode = tool_args.get("mode", "write")
    encoding = tool_args.get("encoding", "utf-8")
    
    console.info(f"write_file: {file_path}, mode: {mode}")
    
    try:
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        
        write_mode = "a" if mode == "append" else "w"
        with open(file_path, write_mode, encoding=encoding) as f:
            f.write(content)
        
        action = "追加" if mode == "append" else "写入"
        console.success(f"write_file 成功: {action} {len(content)} 字符")
        return {"result": f"文件{action}成功: {file_path}", "error": None}
    
    except Exception as e:
        console.error(f"write_file 失败: {e}")
        return {"result": None, "error": f"写入文件失败: {str(e)}"}


def _execute_delete_file(tool_args: dict) -> dict:
    """执行 delete_file 工具"""
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    console.info(f"delete_file: {file_path}")
    
    try:
        if not os.path.exists(file_path):
            return {"result": None, "error": f"路径不存在: {file_path}"}
        
        if os.path.isfile(file_path):
            os.remove(file_path)
            console.success("delete_file 成功: 已删除文件")
            return {"result": f"文件已删除: {file_path}", "error": None}
        elif os.path.isdir(file_path):
            shutil.rmtree(file_path)
            console.success("delete_file 成功: 已删除目录及其内容")
            return {"result": f"目录已删除: {file_path}", "error": None}
        else:
            return {"result": None, "error": f"未知文件类型: {file_path}"}
    
    except Exception as e:
        print(f"[ToolExec] delete_file 失败: {e}")
        return {"result": None, "error": f"删除失败: {str(e)}"}


def _execute_list_dir(tool_args: dict) -> dict:
    """执行 list_dir 工具"""
    dir_path = tool_args.get("directory") or tool_args.get("path") or tool_args.get("dir_path")
    if not dir_path:
        return {"result": None, "error": "缺少 directory 参数"}
    
    recursive = tool_args.get("recursive", False)
    show_hidden = tool_args.get("show_hidden", False)
    
    print(f"[ToolExec] list_dir: {dir_path}, recursive: {recursive}")
    
    try:
        if not os.path.exists(dir_path):
            return {"result": None, "error": f"目录不存在: {dir_path}"}
        
        if not os.path.isdir(dir_path):
            return {"result": None, "error": f"路径不是目录: {dir_path}"}
        
        result_lines = []
        file_count = 0
        dir_count = 0
        
        if recursive:
            for root, dirs, files in os.walk(dir_path):
                if not show_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files = [f for f in files if not f.startswith(".")]
                
                rel_root = os.path.relpath(root, dir_path)
                if rel_root == ".":
                    rel_root = ""
                
                for d in sorted(dirs):
                    dir_count += 1
                    prefix = f"{rel_root}/" if rel_root else ""
                    result_lines.append(f"📁 {prefix}{d}/")
                
                for f in sorted(files):
                    file_count += 1
                    prefix = f"{rel_root}/" if rel_root else ""
                    result_lines.append(f"📄 {prefix}{f}")
        else:
            entries = os.listdir(dir_path)
            if not show_hidden:
                entries = [e for e in entries if not e.startswith(".")]
            
            for entry in sorted(entries):
                full_path = os.path.join(dir_path, entry)
                if os.path.isdir(full_path):
                    dir_count += 1
                    result_lines.append(f"📁 {entry}/")
                else:
                    file_count += 1
                    result_lines.append(f"📄 {entry}")
        
        summary = f"目录: {dir_path}\n共 {dir_count} 个目录, {file_count} 个文件"
        content = "\n".join(result_lines) if result_lines else "(空目录)"
        
        print(f"[ToolExec] list_dir 成功: {dir_count} 目录, {file_count} 文件")
        return {"result": f"{summary}\n\n{content}", "error": None}
    
    except Exception as e:
        print(f"[ToolExec] list_dir 失败: {e}")
        return {"result": None, "error": f"列出目录失败: {str(e)}"}


def _execute_create_dir(tool_args: dict) -> dict:
    """执行 create_dir 工具"""
    dir_path = tool_args.get("directory") or tool_args.get("path") or tool_args.get("dir_path")
    if not dir_path:
        return {"result": None, "error": "缺少 directory 参数"}
    
    print(f"[ToolExec] create_dir: {dir_path}")
    
    try:
        if os.path.exists(dir_path):
            if os.path.isdir(dir_path):
                return {"result": f"目录已存在: {dir_path}", "error": None}
            else:
                return {"result": None, "error": f"路径已存在但不是目录: {dir_path}"}
        
        os.makedirs(dir_path, exist_ok=True)
        print(f"[ToolExec] create_dir 成功")
        return {"result": f"目录已创建: {dir_path}", "error": None}
    
    except Exception as e:
        print(f"[ToolExec] create_dir 失败: {e}")
        return {"result": None, "error": f"创建目录失败: {str(e)}"}


def _execute_explore_code(tool_args: dict) -> dict:
    """执行 explore_code 工具"""
    import glob as glob_module
    import re
    
    workspace_root = tool_args.get("workspace_root", ".")
    query = tool_args.get("query", "")
    search_type = tool_args.get("search_type", "file")
    max_results = tool_args.get("max_results", 20)
    file_pattern = tool_args.get("file_pattern", "**/*.py")
    
    print(f"[ToolExec] explore_code: query={query}, type={search_type}")
    
    try:
        findings = []
        
        if search_type == "file":
            pattern = file_pattern if file_pattern else "**/*"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            for m in matches[:max_results]:
                if os.path.isfile(m):
                    rel_path = os.path.relpath(m, workspace_root)
                    findings.append({
                        "path": rel_path,
                        "type": "file",
                        "match": os.path.basename(m)
                    })
        
        elif search_type == "code":
            if not query:
                return {"result": None, "error": "code 搜索需要 query 参数"}
            
            pattern = file_pattern if file_pattern else "**/*.py"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            
            try:
                regex = re.compile(query, re.IGNORECASE)
            except re.error:
                regex = re.compile(re.escape(query), re.IGNORECASE)
            
            for file_path in matches:
                if not os.path.isfile(file_path):
                    continue
                if len(findings) >= max_results:
                    break
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                rel_path = os.path.relpath(file_path, workspace_root)
                                findings.append({
                                    "path": rel_path,
                                    "type": "code",
                                    "line": line_num,
                                    "content": line.strip()[:100]
                                })
                                if len(findings) >= max_results:
                                    break
                except Exception:
                    continue
        
        elif search_type == "structure":
            pattern = file_pattern if file_pattern else "**/*.py"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            
            structure_patterns = {
                "class": re.compile(r"^\s*class\s+(\w+)"),
                "def": re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),
                "import": re.compile(r"^\s*(?:from|import)\s+([\w.]+)"),
            }
            
            for file_path in matches:
                if not os.path.isfile(file_path):
                    continue
                if len(findings) >= max_results:
                    break
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        rel_path = os.path.relpath(file_path, workspace_root)
                        file_structures = {"path": rel_path, "classes": [], "functions": [], "imports": []}
                        
                        for line in f:
                            for struct_type, pattern in structure_patterns.items():
                                match = pattern.match(line)
                                if match:
                                    file_structures[f"{struct_type}s"].append(match.group(1))
                        
                        if any([file_structures["classes"], file_structures["functions"], file_structures["imports"]]):
                            findings.append(file_structures)
                except Exception:
                    continue
        
        else:
            return {"result": None, "error": f"不支持的搜索类型: {search_type}"}
        
        if not findings:
            return {"result": "未找到匹配结果", "error": None}
        
        result_lines = [f"探索结果 (类型: {search_type}, 共 {len(findings)} 项):\n"]
        
        for item in findings:
            if item["type"] == "file":
                result_lines.append(f"  📄 {item['path']}")
            elif item["type"] == "code":
                result_lines.append(f"  📍 {item['path']}:{item['line']}")
                result_lines.append(f"     {item['content']}")
            elif "classes" in item:
                result_lines.append(f"  📁 {item['path']}")
                if item["classes"]:
                    result_lines.append(f"     Classes: {', '.join(item['classes'][:5])}")
                if item["functions"]:
                    result_lines.append(f"     Functions: {', '.join(item['functions'][:5])}")
        
        result = "\n".join(result_lines)
        print(f"[ToolExec] explore_code 成功: {len(findings)} 项结果")
        return {"result": result, "error": None}
    
    except Exception as e:
        print(f"[ToolExec] explore_code 失败: {e}")
        return {"result": None, "error": f"探索失败: {str(e)}"}


def _execute_explore_internet(tool_args: dict) -> dict:
    """执行 explore_internet 工具 - 使用 DuckDuckGo 搜索互联网"""
    query = tool_args.get("query") or tool_args.get("description") or tool_args.get("task_description")
    if not query:
        return {"result": None, "error": "缺少 query 参数"}
    
    max_results = tool_args.get("max_results", 5)
    
    print(f"[ToolExec] explore_internet: {query}, max_results: {max_results}")
    
    try:
        from duckduckgo_search import DDGS
        
        results = []
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=max_results))
        
        if not search_results:
            return {"result": "未找到相关结果", "error": None}
        
        result_lines = [f"互联网搜索结果 (查询: {query}, 共 {len(search_results)} 项):\n"]
        
        for i, item in enumerate(search_results, 1):
            title = item.get("title", "无标题")
            href = item.get("href", "")
            body = item.get("body", "")
            
            result_lines.append(f"{i}. {title}")
            if href:
                result_lines.append(f"   链接: {href}")
            if body:
                truncated_body = body[:300] + "..." if len(body) > 300 else body
                result_lines.append(f"   摘要: {truncated_body}")
            result_lines.append("")
        
        result = "\n".join(result_lines)
        print(f"[ToolExec] explore_internet 成功: {len(search_results)} 项结果")
        return {"result": result, "error": None}
    
    except ImportError:
        error_msg = "duckduckgo-search 库未安装，请运行: pip install duckduckgo-search"
        print(f"[ToolExec] explore_internet 失败: {error_msg}")
        return {"result": None, "error": error_msg}
    
    except Exception as e:
        print(f"[ToolExec] explore_internet 失败: {e}")
        return {"result": None, "error": f"搜索失败: {str(e)}"}


def _execute_call_explore_agent(tool_args: dict, llm_service=None, token_callback: Optional[Callable[[str], None]] = None, message_context: dict = None) -> dict:
    """执行 call_explore_agent 工具 - 切换到探索 Agent Graph"""
    task_description = tool_args.get("task_description")
    if not task_description:
        return {"result": None, "error": "缺少 task_description 参数"}

    print(f"[ToolExec] call_explore_agent: {task_description}")

    if llm_service is None:
        return {"result": None, "error": "LLM 服务未配置，无法执行子代理任务"}

    workspace_id = None
    parent_chain_messages = []
    current_conversation_messages = []
    settings_service = None
    if message_context:
        workspace_id = message_context.get("workspace_id")
        parent_chain_messages = message_context.get("parent_chain_messages") or []
        current_conversation_messages = message_context.get("current_conversation_messages") or []
        settings_service = message_context.get("settings_service")

    if not workspace_id:
        return {"result": None, "error": "缺少 workspace_id，无法切换到探索 Agent Graph"}

    try:
        from ..agent_graphs import run_agent_graph
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent_graph,
                "explore_agent",
                task_description,
                workspace_id,
                llm_service,
                token_callback,
                "accumulate",
                3,
                settings_service,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
                False,
            )
            try:
                outcome = future.result(timeout=45)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": "explore_agent 子图执行超时",
                        "details": {"agent_type": "explore_agent", "timeout_seconds": 45},
                    },
                }
        if outcome.get("status") == "failed":
            exit_info = outcome.get("exit_info") or {}
            error_msg = exit_info.get("message") or exit_info.get("code") or "子代理执行失败"
            print(f"[ToolExec] call_explore_agent 失败: {error_msg}")
            return {"result": None, "error": error_msg, "outcome": outcome}
        result = outcome.get("payload") or ""
        print(f"[ToolExec] call_explore_agent 完成")
        return {"result": result, "error": None, "outcome": outcome}

    except Exception as e:
        print(f"[ToolExec] call_explore_agent 失败: {e}")
        return {"result": None, "error": f"子代理执行失败: {str(e)}"}


def _execute_call_review_agent(tool_args: dict, llm_service=None, token_callback: Optional[Callable[[str], None]] = None, message_context: dict = None) -> dict:
    """执行 call_review_agent 工具 - 切换到审查 Agent Graph"""
    task_description = tool_args.get("task_description")
    if not task_description:
        return {"result": None, "error": "缺少 task_description 参数"}

    print(f"[ToolExec] call_review_agent: {task_description}")

    if llm_service is None:
        return {"result": None, "error": "LLM 服务未配置，无法执行子代理任务"}

    workspace_id = None
    parent_chain_messages = []
    current_conversation_messages = []
    settings_service = None
    if message_context:
        workspace_id = message_context.get("workspace_id")
        parent_chain_messages = message_context.get("parent_chain_messages") or []
        current_conversation_messages = message_context.get("current_conversation_messages") or []
        settings_service = message_context.get("settings_service")

    if not workspace_id:
        return {"result": None, "error": "缺少 workspace_id，无法切换到审查 Agent Graph"}

    try:
        from ..agent_graphs import run_agent_graph
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent_graph,
                "review_agent",
                task_description,
                workspace_id,
                llm_service,
                token_callback,
                "accumulate",
                3,
                settings_service,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
                False,
            )
            try:
                outcome = future.result(timeout=45)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": "review_agent 子图执行超时",
                        "details": {"agent_type": "review_agent", "timeout_seconds": 45},
                    },
                }
        if outcome.get("status") == "failed":
            exit_info = outcome.get("exit_info") or {}
            error_msg = exit_info.get("message") or exit_info.get("code") or "子代理执行失败"
            print(f"[ToolExec] call_review_agent 失败: {error_msg}")
            return {"result": None, "error": error_msg, "outcome": outcome}
        result = outcome.get("payload") or ""
        print(f"[ToolExec] call_review_agent 完成")
        return {"result": result, "error": None, "outcome": outcome}

    except Exception as e:
        print(f"[ToolExec] call_review_agent 失败: {e}")
        return {"result": None, "error": f"子代理执行失败: {str(e)}"}


def _execute_workspace_tool(tool_name: str, tool_args: dict, workspace_id: str, workspace_service=None) -> dict:
    """执行 workspace 相关工具"""
    console.section(f"Workspace 工具: {tool_name}")
    
    if workspace_service is None:
        from ...service import WorkspaceService
        workspace_service = WorkspaceService()
    
    if tool_name == "list_workspace_files":
        return _execute_list_workspace_files(workspace_id, workspace_service)
    elif tool_name == "get_workspace_info":
        return _execute_get_workspace_info(workspace_id, workspace_service)
    elif tool_name == "search_files":
        return _execute_search_files(tool_args, workspace_id, workspace_service)
    else:
        return {"result": None, "error": f"未知的 workspace 工具: {tool_name}"}


def _execute_list_workspace_files(workspace_id: str, workspace_service) -> dict:
    """列出工作区文件"""
    console.info(f"列出工作区文件: {workspace_id}")
    
    success, files, error_msg = workspace_service.list_files(workspace_id)
    
    if not success:
        console.error(f"列出文件失败: {error_msg}")
        return {"result": None, "error": error_msg}
    
    if not files:
        console.success("工作区为空")
        return {"result": "工作区为空，暂无文件", "error": None}
    
    result_lines = ["工作区文件列表：\n"]
    for f in files:
        icon = "📁" if f["is_dir"] else "📄"
        size_str = "" if f["is_dir"] else f" ({_format_file_size(f['size'])})"
        result_lines.append(f"  {icon} {f['path']}{size_str}")
    
    result = "\n".join(result_lines)
    console.success(f"找到 {len(files)} 个文件/目录")
    return {"result": result, "error": None}


def _execute_get_workspace_info(workspace_id: str, workspace_service) -> dict:
    """获取工作区信息"""
    console.info(f"获取工作区信息: {workspace_id}")
    
    info = workspace_service.get_workspace_info(workspace_id)
    if not info:
        console.error(f"工作区不存在: {workspace_id}")
        return {"result": None, "error": f"工作区不存在: {workspace_id}"}
    
    workspace_dir = workspace_service.get_workspace_dir(workspace_id)
    
    result_lines = [
        "工作区信息：",
        f"  ID: {info.get('id')}",
        f"  会话ID: {info.get('session_id')}",
        f"  状态: {info.get('status')}",
        f"  路径: {workspace_dir}",
    ]
    
    if workspace_dir and os.path.exists(workspace_dir):
        total_size = 0
        file_count = 0
        dir_count = 0
        for root, dirs, files in os.walk(workspace_dir):
            dir_count += len(dirs)
            for f in files:
                file_count += 1
                total_size += os.path.getsize(os.path.join(root, f))
        result_lines.extend([
            f"  文件数: {file_count}",
            f"  目录数: {dir_count}",
            f"  总大小: {_format_file_size(total_size)}",
        ])
    
    result = "\n".join(result_lines)
    console.success("获取工作区信息成功")
    return {"result": result, "error": None}


def _execute_search_files(tool_args: dict, workspace_id: str, workspace_service) -> dict:
    """在工作区内搜索文件"""
    pattern = tool_args.get("pattern", "*")
    console.info(f"搜索文件: pattern={pattern}, workspace={workspace_id}")
    
    workspace_dir = workspace_service.get_workspace_dir(workspace_id)
    if not workspace_dir:
        console.error(f"工作区不存在: {workspace_id}")
        return {"result": None, "error": f"工作区不存在: {workspace_id}"}
    
    if not os.path.exists(workspace_dir):
        console.success("工作区目录不存在，无文件")
        return {"result": "工作区为空", "error": None}
    
    matches = []
    for root, dirs, files in os.walk(workspace_dir):
        for filename in files:
            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, workspace_dir)
                matches.append({
                    "name": filename,
                    "path": rel_path.replace("\\", "/"),
                    "size": os.path.getsize(full_path),
                })
        for dirname in dirs:
            if fnmatch.fnmatch(dirname.lower(), pattern.lower()):
                full_path = os.path.join(root, dirname)
                rel_path = os.path.relpath(full_path, workspace_dir)
                matches.append({
                    "name": dirname,
                    "path": rel_path.replace("\\", "/"),
                    "is_dir": True,
                })
    
    if not matches:
        console.success(f"未找到匹配 '{pattern}' 的文件")
        return {"result": f"未找到匹配 '{pattern}' 的文件", "error": None}
    
    result_lines = [f"找到 {len(matches)} 个匹配 '{pattern}' 的结果：\n"]
    for m in matches:
        icon = "📁" if m.get("is_dir") else "📄"
        size_str = "" if m.get("is_dir") else f" ({_format_file_size(m['size'])})"
        result_lines.append(f"  {icon} {m['path']}{size_str}")
    
    result = "\n".join(result_lines)
    console.success(f"找到 {len(matches)} 个匹配项")
    return {"result": result, "error": None}


def _format_file_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def check_doom_loop(state: ToolExecutionState) -> dict:
    """DoomLoop 检测"""
    print("[ToolExec] DoomLoop 检测...")
    
    tool_name = state["tool_name"]
    tool_args = state["tool_args"]
    previous_calls = state.get("previous_calls", [])
    
    duplicate_count = 0
    for call in previous_calls:
        if call["tool"] == tool_name and call["args"] == tool_args:
            duplicate_count += 1
    
    if duplicate_count >= 3:
        print("[ToolExec] 检测到 DoomLoop!")
        return {"doom_loop_detected": True, "error": "DoomLoop detected"}
    
    print("[ToolExec] DoomLoop 检测通过")
    return {"doom_loop_detected": False}


def create_tool_execution_subgraph(workspace_service=None, llm_service=None, token_callback: Optional[Callable[[str], None]] = None, settings_service=None, message_context: dict = None):
    """创建工具执行子图"""
    graph = StateGraph(ToolExecutionState)
    
    def check_permission_node(state: ToolExecutionState) -> dict:
        return check_permission(state, workspace_service, settings_service)
    
    def execute_tool_node(state: ToolExecutionState) -> dict:
        return execute_tool(state, workspace_service, llm_service, token_callback, message_context)
    
    def deny_execution_node(state: ToolExecutionState) -> dict:
        return deny_execution(state, message_context)
    
    def doom_loop_check_node(state: ToolExecutionState) -> dict:
        result = check_doom_loop(state)
        if result.get("doom_loop_detected"):
            if message_context:
                send_message = message_context.get("send_message")
                if send_message:
                    send_message("DoomLoop detected: repeated tool calls", SegmentType.ERROR, {"source": "doom_loop"})
        return result
    
    graph.add_node("check_permission", check_permission_node)
    graph.add_node("ask_user", ask_user)
    graph.add_node("deny", deny_execution_node)
    graph.add_node("execute", execute_tool_node)
    graph.add_node("doom_loop_check", doom_loop_check_node)
    
    graph.set_entry_point("check_permission")
    
    graph.add_conditional_edges(
        "check_permission",
        route_by_permission,
        {"allow": "execute", "ask": "ask_user", "deny": "deny"}
    )
    
    graph.add_edge("ask_user", "execute")
    graph.add_edge("execute", "doom_loop_check")
    graph.add_edge("doom_loop_check", END)
    graph.add_edge("deny", END)
    
    return graph.compile()


def run_tool_execution(
    tool_name: str,
    tool_args: dict,
    workspace_id: str,
    previous_calls: List[ToolCall] = None,
    workspace_service=None,
    llm_service=None,
    token_callback: Optional[Callable[[str], None]] = None,
    task_description: str = "",
    previous_results: List[str] = None,
    agent_type: str = "build_agent",
    settings_service=None,
    message_context: dict = None
) -> dict:
    """
    运行工具执行子图
    
    Args:
        tool_name: 工具名称
        tool_args: 工具参数
        workspace_id: 工作区ID
        previous_calls: 之前的工具调用记录
        workspace_service: 工作区服务实例
        llm_service: LLM 服务实例
        token_callback: 流式输出回调
        task_description: 任务描述（用于思考工具）
        previous_results: 之前任务的执行结果（短期记忆）
        agent_type: Agent 类型
        settings_service: 设置服务实例
        message_context: 消息上下文，包含 send_message 等方法
        
    Returns:
        执行结果
    """
    print("\n" + "="*60)
    print("[Subgraph] 工具执行子图启动")
    print("="*60)
    
    initial_state: ToolExecutionState = {
        "tool_name": tool_name,
        "tool_args": tool_args,
        "workspace_id": workspace_id,
        "permission": "pending",
        "result": None,
        "error": None,
        "doom_loop_detected": False,
        "previous_calls": previous_calls or [],
        "task_description": task_description,
        "previous_results": previous_results or [],
        "agent_type": agent_type,
    }
    
    graph = create_tool_execution_subgraph(workspace_service, llm_service, token_callback, settings_service, message_context)
    result = graph.invoke(initial_state)
    
    print("="*60)
    print("[Subgraph] 工具执行子图完成")
    print("="*60)
    
    return result
