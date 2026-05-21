"""
Prompts 包
统一管理所有提示词相关模块
"""

from .agent_prompts import (
    GENERAL_PURPOSE_PROMPT,
    EXPLORE_AGENT_PROMPT,
    PLAN_AGENT_PROMPT,
    REVIEW_AGENT_PROMPT,
    PREDICTION_AGENT_PROMPT
)
from .system_prompts import get_agent_prompt, enhance_prompt_with_context, AGENT_PROMPTS
from .compression_prompts import CONVOLUTION_COMPRESSION_PROMPT, COMPRESSION_SYSTEM_PROMPT
from .templates import (
    DIRECT_SYSTEM_PROMPT,
    PLAN_MODE_SYSTEM_PROMPT,
    THINK_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    INTENT_ANALYSIS_PROMPT,
    UserTemplateManager,
    ToolSchemaManager,
    MessageProcessor,
)
from .graph_prompts import (
    build_chat_system_prompt as _graph_build_chat_system_prompt,
    build_context_prompt as _graph_build_context_prompt,
    build_direct_chat_messages as _graph_build_direct_chat_messages,
)

__all__ = [
    # Agent prompts
    "GENERAL_PURPOSE_PROMPT",
    "EXPLORE_AGENT_PROMPT",
    "PLAN_AGENT_PROMPT",
    "REVIEW_AGENT_PROMPT",
    "PREDICTION_AGENT_PROMPT",
    "AGENT_PROMPTS",
    "get_agent_prompt",
    "enhance_prompt_with_context",
    # Compression prompts
    "CONVOLUTION_COMPRESSION_PROMPT",
    "COMPRESSION_SYSTEM_PROMPT",
    # System prompts (from templates)
    "DIRECT_SYSTEM_PROMPT",
    "PLAN_MODE_SYSTEM_PROMPT",
    "THINK_SYSTEM_PROMPT",
    "CHAT_SYSTEM_PROMPT",
    "INTENT_ANALYSIS_PROMPT",
    # Template managers
    "UserTemplateManager",
    "ToolSchemaManager",
    "MessageProcessor",
    # Graph prompts (internal)
    "_graph_build_chat_system_prompt",
    "_graph_build_context_prompt",
    "_graph_build_direct_chat_messages",
]
