"""finalize 节点：发布最终文本（CHAT_START/DELTA/END）并设置 final_reply，随后 END。"""

from __future__ import annotations

from typing import Optional

from ...state import AgentState
from service.session_service.canonical import SegmentType


def create_finalize_node(message_context: Optional[dict] = None):
    def finalize_node(state: AgentState) -> dict:
        text = state.get("pending_final_text") or state.get("final_reply") or ""
        if message_context:
            send_message = message_context.get("send_message")
            if send_message:
                send_message("", SegmentType.CHAT_START, {
                    "task_description": "输出最终回复",
                    "is_start": True,
                })
                if text:
                    send_message(text, SegmentType.CHAT_DELTA, {
                        "task_description": "输出最终回复",
                        "is_delta": True,
                    })
                send_message("", SegmentType.CHAT_END, {
                    "task_description": "输出最终回复",
                    "is_end": True,
                    "result": text,
                })
        return {
            "final_reply": text,
            "pending_final_text": text,
            "has_tool_use": False,
            "pending_tools": [],
            "pending_batch": None,
            "next_action": None,
            "_route_target": "done",
        }

    return finalize_node
