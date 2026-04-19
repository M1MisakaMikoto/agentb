"""
Tool Registry - 工具定义和权限管理

包含：
- 工具定义 ALL_TOOLS
- 权限检查函数
- 辅助常量和函数
"""
from typing import List, Optional, Literal, Any
from datetime import datetime, timezone
import json

from service.session_service.canonical import SegmentType


FILE_TOOLS = {"read_file", "write_file", "delete_file", "list_dir", "create_dir", "read_document"}
EXPLORE_TOOLS = {"explore_code", "explore_internet"}
SUBAGENT_TOOLS = {"call_explore_agent", "call_review_agent"}
WORKSPACE_TOOLS = {"list_workspace_files", "get_workspace_info", "search_files"}
TODO_TOOLS = {"update_todo"}
MODE_TOOLS = {"switch_execution_mode"}
SQL_TOOLS = {"sql_query"}

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


def _summarize_text(value: Any, limit: int = 160) -> str:
    if value is None:
        raw = ""
    elif isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False)
        except Exception:
            raw = str(value)
    compact = " ".join(raw.split())
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
        "director_agent": ["read_file", "write_file", "delete_file", "list_dir", "create_dir", "explore_code", "explore_internet", "thinking", "chat", "call_explore_agent", "call_review_agent", "list_workspace_files", "get_workspace_info", "search_files", "update_todo", "switch_execution_mode", "rag_search", "read_document", "sql_query"],
        "plan_agent": ["read_file", "write_file", "list_dir", "explore_code", "thinking", "chat", "call_explore_agent", "call_review_agent", "rag_search", "read_document", "sql_query", "switch_execution_mode"],
        "review_agent": ["read_file", "list_dir", "explore_code", "thinking", "chat", "sql_query"],
        "explore_agent": ["read_file", "list_dir", "thinking", "chat", "explore_internet", "list_workspace_files", "get_workspace_info", "search_files", "sql_query"],
        "admin_agent": ["read_file", "write_file", "delete_file", "list_dir", "create_dir", "explore_code", "explore_internet", "thinking", "chat", "call_explore_agent", "call_review_agent", "list_workspace_files", "get_workspace_info", "search_files", "sql_query"]
    }
    return default_permissions.get(agent_type, default_permissions["director_agent"])


def filter_tools_by_agent_type(agent_type: str, settings_service=None) -> List[dict]:
    from ...tools import ALL_TOOLS
    allowed_tools = get_allowed_tools(agent_type, settings_service)
    return [ALL_TOOLS[name] for name in allowed_tools if name in ALL_TOOLS]


def generate_tool_prompt(agent_type: str, settings_service=None) -> str:
    tools = filter_tools_by_agent_type(agent_type, settings_service)
    lines = ["工具列表："]
    for tool in tools:
        if tool["params"]:
            lines.append(tool["params"])
    result = "\n".join(lines)
    print(f"[Tool Prompt] agent_type={agent_type}, tools={[t['name'] for t in tools]}")
    return result


def is_tool_allowed(tool_name: str, agent_type: str, settings_service=None) -> bool:
    allowed_tools = get_allowed_tools(agent_type, settings_service)
    return tool_name in allowed_tools
