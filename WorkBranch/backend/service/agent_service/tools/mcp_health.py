"""MCP 服务可用性状态（进程内）。

用途：
- 启动探活失败时记录告警状态（后端仍可启动）；
- /health 暴露状态；
- 每次调用 MCP 工具失败时累加 failure_count 并重复告警。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

SERVICE_NAME = "upstream_tools"

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "service": SERVICE_NAME,
    "available": None,
    "error": None,
    "checked_at": None,
    "failure_count": 0,
    "registered": [],
    "unlisted": [],
    "missing": [],
}


def record_success() -> None:
    with _lock:
        _state["available"] = True
        _state["error"] = None
        _state["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def record_inventory(registered: list, unlisted: list, missing: list) -> None:
    """记录本次工具登记分布：已登记 / 未登记被忽略 / 配置登记但服务缺失。"""
    with _lock:
        _state["registered"] = list(registered)
        _state["unlisted"] = list(unlisted)
        _state["missing"] = list(missing)


def record_failure(error: str) -> int:
    """记录一次失败，返回累计失败次数。"""
    with _lock:
        _state["available"] = False
        _state["error"] = str(error)
        _state["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _state["failure_count"] = int(_state.get("failure_count") or 0) + 1
        return _state["failure_count"]


def snapshot() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def alert_message() -> Optional[str]:
    """不可用时返回面向用户的告警文本；可用或未探活时返回 None。"""
    state = snapshot()
    if state.get("available") is not False:
        return None
    return (
        f"上游工具 MCP 服务（{state['service']}）当前不可用：{state.get('error') or '未知原因'}。"
        "报告上传、巡查记录、AI 研判等依赖该服务的功能会在调用时失败。"
    )


def reset() -> None:
    """测试用：重置状态。"""
    with _lock:
        _state.update(
            {
                "available": None,
                "error": None,
                "checked_at": None,
                "failure_count": 0,
                "registered": [],
                "unlisted": [],
                "missing": [],
            }
        )
