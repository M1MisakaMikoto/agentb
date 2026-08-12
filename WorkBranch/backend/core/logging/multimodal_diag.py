"""多模态通道诊断日志。

两个输出时点：
1. 图像装配触发时（resolve_runtime_parts 之后）：输出开关状态、图像数量与
   名称、预判路由；
2. v4 路由判定后：输出实际是否走原生多模态 chat。

写入 llm_decision_trace.log（trace 日志）与控制台，便于判断 image 专用通道
是否正常工作。
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from core.logging import console, open_trace_log
from service.session_service.message_content import summarize_image_parts


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def _image_summary_text(summary: Dict[str, Any]) -> str:
    if summary["count"] <= 0:
        return "未取得图像"
    names = ", ".join(summary["names"])
    return f"取得图像{summary['count']}张 [{names}]"


def log_multimodal_channel_check(
    *,
    parts: Any,
    conversation_id: Optional[str],
    supports_vision: bool,
) -> None:
    """图像装配触发时输出：是否开启图像理解支持 + 图像摘要。"""
    ts = _now()
    summary = summarize_image_parts(parts)
    enabled_text = "当前已开启图像理解" if supports_vision else "当前未开启图像理解"
    image_text = _image_summary_text(summary)
    line = f"[{ts}] {enabled_text}（supports_vision={supports_vision}）：{image_text}"
    try:
        with open_trace_log() as f:
            f.write(f"\n[{ts}] === 🖼️ MULTIMODAL CHANNEL CHECK ===\n")
            f.write(line + "\n")
            if conversation_id:
                f.write(f"[{ts}] conversation_id: {conversation_id}\n")
            f.flush()
    except Exception:
        pass
    console.info(f"[multimodal-check] {line}")


def log_multimodal_route_result(
    *,
    parts: Any,
    conversation_id: Optional[str],
    supports_vision: bool,
    agent_type: str,
    routed_native: bool,
) -> None:
    """v4 路由判定后输出：实际是否走原生多模态 chat。"""
    ts = _now()
    summary = summarize_image_parts(parts)
    image_text = _image_summary_text(summary)
    route_text = "原生多模态 chat（→finalize）" if routed_native else "正常决策链（→reasoning）"
    reasons = []
    if not supports_vision:
        reasons.append("未开启多模态")
    if agent_type != "director_agent":
        reasons.append(f"agent_type={agent_type}（非 director_agent）")
    if not routed_native and summary["count"] <= 0:
        reasons.append("无图像输入")
    reason_text = f"（原因: {'; '.join(reasons)}）" if reasons else ""
    line = (
        f"[{ts}] supports_vision={supports_vision}, agent_type={agent_type}，"
        f"{image_text}；实际路由: {route_text}{reason_text}"
    )
    try:
        with open_trace_log() as f:
            f.write(f"\n[{ts}] === 🖼️ MULTIMODAL ROUTE RESULT ===\n")
            f.write(line + "\n")
            if conversation_id:
                f.write(f"[{ts}] conversation_id: {conversation_id}\n")
            f.flush()
    except Exception:
        pass
    console.info(f"[multimodal-route] {line}")

def log_multimodal_tool_executed(
    *,
    image_path: str,
    conversation_id: Optional[str],
) -> None:
    """analyze_image 工具执行时输出：图像装载进原生多模态 chat。"""
    ts = _now()
    line = f"[{ts}] 工具 analyze_image 装载图像 [{image_path}] → 原生多模态 chat（工具）"
    try:
        with open_trace_log() as f:
            f.write(f"\n[{ts}] === 🖼️ MULTIMODAL TOOL EXECUTED ===\n")
            f.write(line + "\n")
            if conversation_id:
                f.write(f"[{ts}] conversation_id: {conversation_id}\n")
            f.flush()
    except Exception:
        pass
    console.info(f"[multimodal-tool] {line}")
