"""
sidekick-analyze 节点（V4 定稿）

前置处理：恶意检测 / 意图分析 / 请求改写。恶意检测与意图分析已在
agent_service.send_message 入口执行（基线修复），本节点负责图内规范化与改写落地：
- 确保 current_user_message_text / user_message 可用；
- 若 intent_analysis 提供 rewritten_query，写入 user_message；
- 不做任何模式路由（DIRECT/PLAN 状态机已退役），固定直连 leader-reasoning。
"""

from __future__ import annotations

from typing import Optional

from ...state import AgentState
from core.logging import console
from core.logging.multimodal_diag import log_multimodal_route_result


def _supports_vision(settings_service) -> bool:
    if settings_service is None:
        return False
    try:
        return bool(settings_service.get("llm:supports_vision"))
    except Exception:
        return False


def create_analyze_node(_llm_service=None, message_context=None, _settings_service=None):
    def analyze_node(state: AgentState) -> dict:
        user_message = (
            state.get("current_user_message_text")
            or state.get("user_message")
            or ""
        )
        if not user_message and state.get("messages"):
            last = state["messages"][-1]
            user_message = str(last.get("content", "")) if isinstance(last, dict) else str(last)

        # 意图改写（若入口意图分析已给出 rewritten_query）
        rewritten = None
        try:
            intent = state.get("intent_analysis") or {}
            if isinstance(intent, dict):
                rewritten = intent.get("rewritten_query")
        except Exception:
            rewritten = None

        final_user_message = rewritten if rewritten and str(rewritten).strip() else user_message

        console.info(f"[sidekick-analyze] 用户问题规范化完成（{len(final_user_message)} 字符）")

        # 图像理解已改为 analyze_image 工具：有图也走 reasoning，由模型决定是否调用工具
        user_message_parts = state.get("current_user_message_parts") or []
        agent_type = state.get("agent_type") or "director_agent"
        supports_vision = _supports_vision(_settings_service)
        log_multimodal_route_result(
            parts=user_message_parts,
            conversation_id=(message_context or {}).get("conversation_id"),
            supports_vision=supports_vision,
            agent_type=agent_type,
            routed_native=False,
        )
        return {
            "current_user_message_text": final_user_message,
            "user_message": final_user_message,
            "_route_target": "reasoning",
        }

    return analyze_node


def route_after_analyze(state: AgentState) -> str:
    return state.get("_route_target") or "reasoning"
