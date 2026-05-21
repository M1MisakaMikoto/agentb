"""
Prompt 模板层
包含 System Prompt 模板和 User Message 模板
"""

from .system_prompts import (
    DIRECT_SYSTEM_PROMPT,
    PLAN_MODE_SYSTEM_PROMPT,
    THINK_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    INTENT_ANALYSIS_PROMPT,
)
from .user_templates import UserTemplateManager
from .tool_schemas import ToolSchemaManager
from ..base.message_processor import MessageProcessor

__all__ = [
    "DIRECT_SYSTEM_PROMPT",
    "PLAN_MODE_SYSTEM_PROMPT",
    "THINK_SYSTEM_PROMPT",
    "CHAT_SYSTEM_PROMPT",
    "INTENT_ANALYSIS_PROMPT",
    "UserTemplateManager",
    "ToolSchemaManager",
    "MessageProcessor",
]