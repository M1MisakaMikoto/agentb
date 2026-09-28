"""MCP 客户端：把上游工具 MCP 服务的工具接入本系统工具表。

- 启动时（或首次使用时）通过 list_tools 获取工具名与 inputSchema，
  转译成与现有提示词一致的 params 文本后写入 ALL_TOOLS；
- 调用时通过 call_tool 转发，失败即如实返回错误并记录 MCP 服务告警（不兜底、不缓存）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from core.logging import console
from .mcp_health import record_failure, record_success

# 这些工具已不在 ALL_TOOLS 中静态维护，改由 MCP 服务提供
MCP_TOOL_NAMES = {
    "submit_facility_report",
    "submit_facility_forecast",
    "submit_dailypatrol_record",
    "submit_ai_judgment_issue",
}

_REQUIRED_HINT = "必填"
_OPTIONAL_HINT = "可选"


def _server_settings() -> Dict[str, Any]:
    from singleton import get_settings_service

    settings = get_settings_service()
    group = "agent_tools:mcp:upstream_tools"
    return {
        "url": settings.get(f"{group}:url"),
        "timeout_seconds": float(settings.get(f"{group}:timeout_seconds")),
        "probe_timeout_seconds": float(settings.get(f"{group}:probe_timeout_seconds")),
    }


def render_tool_params(tool) -> str:
    """把 MCP 工具的 inputSchema 转译成提示词里的 params 文本（单行）。"""
    schema = getattr(tool, "inputSchema", None) or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    parts: List[str] = []
    for key, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        description = str(spec.get("description") or spec.get("type") or "").strip()
        hint = _REQUIRED_HINT if key in required else _OPTIONAL_HINT
        if description and any(word in description for word in ("必填", "可选")):
            detail = description
        else:
            detail = f"{description}，{hint}" if description else hint
        parts.append(f'"{key}":"({detail})"')

    if not parts:
        raise ValueError(f"MCP 工具 {getattr(tool, 'name', '?')} 的 inputSchema 没有属性")
    return f"{tool.name}:{{" + ",".join(parts) + "}"


async def _list_tools_async(url: str, timeout_seconds: float) -> list:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url, timeout=timeout_seconds) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return list(result.tools)


async def _call_tool_async(url: str, timeout_seconds: float, tool_name: str, arguments: dict):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url, timeout=timeout_seconds) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


def list_mcp_tools() -> list:
    """同步获取 MCP 工具列表（供启动注册与测试使用）。"""
    settings = _server_settings()
    return asyncio.run(_list_tools_async(settings["url"], settings["timeout_seconds"]))


def register_mcp_tools() -> List[str]:
    """把 MCP 工具写入 ALL_TOOLS；成功返回已注册的工具名列表。"""
    from .registry import ALL_TOOLS

    tools = list_mcp_tools()
    registered: List[str] = []
    for tool in tools:
        if tool.name not in MCP_TOOL_NAMES:
            raise ValueError(
                f"MCP 服务返回了未登记的上游工具：{tool.name}"
                f"（本系统只接入 {sorted(MCP_TOOL_NAMES)}）"
            )
        ALL_TOOLS[tool.name] = {
            "name": tool.name,
            "description": tool.description or "",
            "params": render_tool_params(tool),
        }
        registered.append(tool.name)

    missing = MCP_TOOL_NAMES.difference(registered)
    if missing:
        raise ValueError(f"MCP 服务缺少工具：{sorted(missing)}")
    return registered


def _error_text(call_result) -> str:
    """从 isError 结果中取出可读错误详情。"""
    texts = []
    for block in getattr(call_result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(str(text))
    if not texts:
        structured = getattr(call_result, "structuredContent", None)
        texts.append(
            json.dumps(structured, ensure_ascii=False) if structured else "无错误详情"
        )
    return " | ".join(texts)


def describe_exception(exc: BaseException) -> str:
    """展开 ExceptionGroup，返回最内层可读原因。"""
    nested = getattr(exc, "exceptions", None)
    if nested:
        return describe_exception(nested[0])
    return f"{type(exc).__name__}: {exc}"


def _unwrap_payload(data: Any) -> Any:
    """FastMCP 对未声明 output schema 的工具会把返回值包一层 {"result": ...}。"""
    if (
        isinstance(data, dict)
        and set(data.keys()) == {"result"}
        and isinstance(data["result"], dict)
    ):
        return data["result"]
    return data


def _extract_payload(call_result) -> Any:
    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        return _unwrap_payload(structured)

    for block in getattr(call_result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            import json

            return _unwrap_payload(json.loads(text))
    raise ValueError("MCP 工具调用没有返回可解析的内容")


def execute_mcp_tool(tool_name: str, tool_args: dict) -> dict:
    """执行 MCP 工具；失败时如实返回错误并累加 MCP 服务告警。"""
    import json

    settings = _server_settings()
    try:
        payload = asyncio.run(
            _call_tool_async(
                settings["url"], settings["timeout_seconds"], tool_name, tool_args
            )
        )
    except Exception as e:
        detail = describe_exception(e)
        count = record_failure(detail)
        error = (
            f"MCP 服务不可用：上游工具 MCP 服务调用失败（{detail}）"
            f"，累计失败 {count} 次"
        )
        console.error(f"[mcp] {error}")
        return {"result": None, "error": error, "mcp_service_error": True}

    if getattr(payload, "isError", False):
        # MCP 服务本身可达，是本次调用被服务端拒绝（例如参数校验），不算服务故障
        detail = _error_text(payload)
        console.warning(f"[mcp] 工具 {tool_name} 调用被 MCP 服务拒绝：{detail}")
        return {
            "result": None,
            "error": f"MCP 工具 {tool_name} 调用被拒绝：{detail}",
        }

    try:
        data = _extract_payload(payload)
    except Exception as e:
        return {"result": None, "error": f"MCP 工具返回内容无法解析：{e}"}

    record_success()
    if isinstance(data, dict) and "result" in data:
        return {"result": data.get("result"), "error": data.get("error")}
    return {"result": json.dumps(data, ensure_ascii=False), "error": None}


def probe_mcp_service() -> Optional[str]:
    """启动探活：可用返回 None，不可用返回错误文本（并记录告警状态）。"""
    settings = _server_settings()
    try:
        from .registry import ALL_TOOLS

        tools = asyncio.run(
            _list_tools_async(settings["url"], settings["probe_timeout_seconds"])
        )
        registered: List[str] = []
        for tool in tools:
            if tool.name not in MCP_TOOL_NAMES:
                raise ValueError(f"MCP 服务返回了未登记的上游工具：{tool.name}")
            ALL_TOOLS[tool.name] = {
                "name": tool.name,
                "description": tool.description or "",
                "params": render_tool_params(tool),
            }
            registered.append(tool.name)
        missing = MCP_TOOL_NAMES.difference(registered)
        if missing:
            raise ValueError(f"MCP 服务缺少工具：{sorted(missing)}")
    except Exception as e:
        detail = describe_exception(e)
        count = record_failure(detail)
        message = f"上游工具 MCP 服务探活失败（{detail}），累计失败 {count} 次"
        console.error(f"[mcp] {message}")
        return message
    record_success()
    console.info("[mcp] 上游工具 MCP 服务探活成功，工具已注册")
    return None
