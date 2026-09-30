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
from .mcp_health import record_failure, record_inventory, record_success

# 默认接入的上游工具（等价于 setting.json 里 agent_tools.mcp.upstream_tools.tools 的默认值；
# 实际以配置为准，常量仅供默认配置与测试对照）
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
        "tools": settings.get(f"{group}:tools"),
        "tool_options": settings.get(f"{group}:tool_options"),
    }


def configured_mcp_tools() -> set:
    """从配置读取"登记"到本系统的 MCP 工具名清单（缺失或形状不对直接报错）。"""
    names = _server_settings()["tools"]
    if not isinstance(names, list) or not names:
        raise ValueError(
            "MCP 工具清单未配置或为空：agent_tools.mcp.upstream_tools.tools"
        )
    cleaned = {str(name).strip() for name in names if str(name).strip()}
    if not cleaned:
        raise ValueError(
            "MCP 工具清单为空：agent_tools.mcp.upstream_tools.tools"
        )
    return cleaned


def _tool_option(tool_name: str, key: str, default: Any = None) -> Any:
    options = _server_settings()["tool_options"]
    if options is None:
        return default
    if not isinstance(options, dict):
        raise ValueError(
            "agent_tools.mcp.upstream_tools.tool_options 必须是对象（工具名 -> 选项）"
        )
    entry = options.get(tool_name)
    if entry is None:
        return default
    if not isinstance(entry, dict):
        raise ValueError(f"tool_options.{tool_name} 必须是对象")
    return entry.get(key, default)


def mcp_tool_file_args(tool_name: str) -> List[str]:
    """该工具中需要按工作区解析为绝对路径的参数名（配置驱动，默认空）。"""
    value = _tool_option(tool_name, "file_args", [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"tool_options.{tool_name}.file_args 必须是数组")
    return [str(item) for item in value]


def mcp_tool_timeout_seconds(tool_name: str) -> float:
    """该工具的 MCP 调用超时（可覆盖全局值）。"""
    value = _tool_option(tool_name, "timeout_seconds")
    if value is None:
        return _server_settings()["timeout_seconds"]
    return float(value)


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


def classify_mcp_tools(advertised: list) -> Dict[str, Any]:
    """把服务端自报工具分类：已登记（可写入工具表）/ 未登记（忽略）/ 配置登记但服务缺失。

    - registered：配置清单里有、服务端也提供 → 生成 ALL_TOOLS 条目
    - unlisted：服务端有、配置清单没有 → 告警并忽略（不暴露给 agent）
    - missing：配置清单有、服务端没有 → 告警（该工具不可用，其余工具照常）
    """
    configured = configured_mcp_tools()
    entries: Dict[str, Dict[str, Any]] = {}
    registered: List[str] = []
    unlisted: List[str] = []
    for tool in advertised:
        if tool.name in configured:
            entries[tool.name] = {
                "name": tool.name,
                "description": tool.description or "",
                "params": render_tool_params(tool),
            }
            registered.append(tool.name)
        else:
            unlisted.append(tool.name)
    missing = sorted(configured.difference(registered))
    return {
        "entries": entries,
        "registered": sorted(registered),
        "unlisted": sorted(unlisted),
        "missing": missing,
    }


def register_mcp_tools() -> List[str]:
    """按配置登记 MCP 工具（先分类、再一次性写入工具表，避免半注册）。"""
    from .registry import ALL_TOOLS

    outcome = classify_mcp_tools(list_mcp_tools())
    _warn_scope(outcome)
    ALL_TOOLS.update(outcome["entries"])
    record_inventory(outcome["registered"], outcome["unlisted"], outcome["missing"])
    return outcome["registered"]


def _warn_scope(outcome: Dict[str, Any]) -> None:
    for name in outcome["unlisted"]:
        console.warning(
            f"[mcp] 上游 MCP 服务返回未登记工具，已忽略且不对 agent 暴露：{name}"
            f"（如需接入请加入 agent_tools.mcp.upstream_tools.tools）"
        )
    for name in outcome["missing"]:
        console.warning(
            f"[mcp] 配置已登记但 MCP 服务未提供该工具：{name}（该工具不可用）"
        )


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
    timeout_seconds = mcp_tool_timeout_seconds(tool_name)
    try:
        payload = asyncio.run(
            _call_tool_async(
                settings["url"], timeout_seconds, tool_name, tool_args
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
        tools = asyncio.run(
            _list_tools_async(settings["url"], settings["probe_timeout_seconds"])
        )
        outcome = classify_mcp_tools(tools)
    except Exception as e:
        detail = describe_exception(e)
        count = record_failure(detail)
        message = f"上游工具 MCP 服务探活失败（{detail}），累计失败 {count} 次"
        console.error(f"[mcp] {message}")
        return message

    from .registry import ALL_TOOLS

    ALL_TOOLS.update(outcome["entries"])
    _warn_scope(outcome)
    record_inventory(outcome["registered"], outcome["unlisted"], outcome["missing"])
    record_success()
    console.info(
        f"[mcp] 上游工具 MCP 服务探活成功，已登记 {len(outcome['registered'])} 个工具"
        + (
            f"，忽略未登记 {len(outcome['unlisted'])} 个"
            if outcome["unlisted"]
            else ""
        )
        + (f"，服务缺失 {len(outcome['missing'])} 个" if outcome["missing"] else "")
    )
    return None
