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
from service.session_service.message_content import has_image_parts
from service.agent_service.prompts.graph_prompts import (
    build_chat_system_prompt,
    build_direct_chat_messages,
)


def _supports_vision(settings_service) -> bool:
    if settings_service is None:
        return False
    try:
        return bool(settings_service.get("llm:supports_vision"))
    except Exception:
        return False


def _run_native_multimodal_chat(
    *,
    user_message: str,
    multimodal_parts: list,
    llm_service,
    settings_service,
    message_context,
    state: AgentState,
) -> str:
    """原生多模态：直接以 image_url 调用 LLM，跳过决策链。"""
    if llm_service is None:
        return "无法自动分析图片：LLM 服务未配置。"
    messages = build_direct_chat_messages(
        task_description=user_message,
        parent_chain_messages=state.get("parent_chain_messages") or [],
        current_conversation_messages=state.get("current_conversation_messages") or [],
        multimodal_parts=multimodal_parts,
        message_context=message_context,
    )
    system_prompt = build_chat_system_prompt(settings_service)
    return llm_service.chat(messages=messages, system_prompt=system_prompt)


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

        # 原生多模态：检测到图片输入时直接以 image_url 调用 LLM，跳过决策链
        user_message_parts = state.get("current_user_message_parts") or []
        if (
            (state.get("agent_type") or "director_agent") == "director_agent"
            and _supports_vision(_settings_service)
            and has_image_parts(user_message_parts)
        ):
            console.info("[sidekick-analyze] 检测到图片输入，直接走原生多模态 chat")
            reply = _run_native_multimodal_chat(
                user_message=final_user_message or "请直接分析这张图片并回答用户。",
                multimodal_parts=user_message_parts,
                llm_service=_llm_service,
                settings_service=_settings_service,
                message_context=message_context,
                state=state,
            )
            return {
                "current_user_message_text": final_user_message,
                "user_message": final_user_message,
                "final_reply": reply,
                "pending_final_text": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "_route_target": "finalize",
            }

        return {
            "current_user_message_text": final_user_message,
            "user_message": final_user_message,
            "_route_target": "reasoning",
        }

    return analyze_node


def route_after_analyze(state: AgentState) -> str:
    return state.get("_route_target") or "reasoning"
