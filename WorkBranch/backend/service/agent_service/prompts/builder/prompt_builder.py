"""
PromptBuilder - 提示词三层装配（参考 hermes-agent prompt-assembly 的工程实践）

分层语义：
    stable   : 身份 / 工具指导 / 输出契约。只依赖 (agent_type, mode, tool_schema_prompt)，
               跨轮不变，可安全缓存（含 hash 校验）。
    context  : 会话级上下文（parent_chain / current_conversation / plan 指引）。
               一轮内不变，随会话变化。
    volatile : 本轮状态（workspace / 轮次 / todos / 工具历史 / 当前问题 / 错误注入）。
               每次迭代变化。

结构拆分阶段的目标是“行为不变、边界清晰”：
    - 装配结果与旧 generate_prompt 逐字节一致（由快照测试锁定）；
    - stable 层提供独立 hash 与尺寸统计，为后续启用 provider 前缀缓存铺路。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


@dataclass
class PromptStats:
    """提示词分层统计。"""

    stable_chars: int
    context_chars: int
    volatile_chars: int
    user_message_chars: int
    total_chars: int
    stable_hash: str

    def describe(self) -> str:
        return (
            f"stable={self.stable_chars}ch | context={self.context_chars}ch | "
            f"volatile={self.volatile_chars}ch | user={self.user_message_chars}ch | "
            f"total={self.total_chars}ch | stable_sha256={self.stable_hash[:12]}"
        )


class PromptBuilder:
    """提示词统一装配入口（单例使用或按会话创建均可，无状态内部缓存）。"""

    _stable_cache: dict[tuple[str, str, str], tuple[str, str]] = {}
    _STABLE_CACHE_MAX = 64

    def __init__(self, settings_service=None):
        self._settings = settings_service

    # ---- 分层构建 ----

    def build_stable_system_prompt(self, agent_type: str, mode: str, tool_schema_prompt: str = "") -> str:
        """stable 层：身份 / 工具指导 / 输出契约，跨轮不变。"""
        from ..graph_prompts import _get_system_prompt

        cache_key = (agent_type, mode.upper(), tool_schema_prompt)
        cached = self._stable_cache.get(cache_key)
        if cached is not None:
            return cached[0]

        prompt = _get_system_prompt(agent_type, mode, tool_schema_prompt)
        stable_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        if len(self._stable_cache) >= self._STABLE_CACHE_MAX:
            self._stable_cache.clear()
        self._stable_cache[cache_key] = (prompt, stable_hash)
        return prompt

    def stable_hash(self, agent_type: str, mode: str, tool_schema_prompt: str = "") -> str:
        """返回 stable 层内容的 sha256（用于前缀一致性验证）。"""
        cache_key = (agent_type, mode.upper(), tool_schema_prompt)
        cached = self._stable_cache.get(cache_key)
        if cached is not None:
            return cached[1]
        self.build_stable_system_prompt(agent_type, mode, tool_schema_prompt)
        return self._stable_cache[cache_key][1]

    def build_context_section(
        self,
        parent_chain_messages: Optional[List[dict]] = None,
        current_conversation_messages: Optional[List[dict]] = None,
        message_context: Optional[dict] = None,
    ) -> str:
        """context 层：会话级上下文（压缩后的 parent_chain + 当前会话）。"""
        from ..base.message_processor import MessageProcessor

        processor = MessageProcessor(self._settings)
        parts: list[str] = []

        if parent_chain_messages:
            parent_context = processor.process_conversation_context(
                messages=parent_chain_messages,
                source_type="parent_chain",
                message_context=message_context,
            )
            if parent_context:
                parts.append(parent_context)

        if current_conversation_messages:
            current_context = processor.process_conversation_context(
                messages=current_conversation_messages,
                source_type="current_conversation",
                message_context=message_context,
            )
            if current_context:
                parts.append(current_context)

        return "\n".join(parts)

    def build_user_message(
        self,
        user_message: str,
        workspace_id: str,
        iteration_count: int,
        max_iterations: int,
        tool_history: Optional[List[dict]] = None,
        todos: Optional[List[str]] = None,
        current_todo_index: int = 0,
        plan_content: Optional[str] = None,
        parent_chain_messages: Optional[List[dict]] = None,
        current_conversation_messages: Optional[List[dict]] = None,
        last_error: Any = None,
        mode: str = "DIRECT",
        message_context: Optional[dict] = None,
    ) -> Tuple[str, str]:
        """
        组装 user message，返回 (context_section, user_message_text)。
        context 段独立返回便于统计与后续缓存边界决策。
        """
        from ..base.message_processor import MessageProcessor
        from ..templates.user_templates import UserTemplateManager
        from ..graph_prompts import _format_tool_history

        processor = MessageProcessor(self._settings)

        dynamic_section = processor.build_dynamic_section(
            user_message=user_message,
            workspace_id=workspace_id,
            todos=todos or [],
            current_todo_index=current_todo_index,
            plan_content=plan_content,
            include_iteration=False,
        )

        if mode.upper() == "PLAN":
            dynamic_section += "\n\n" + UserTemplateManager.build_plan_mode_suffix()

        context_section = self.build_context_section(
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            message_context=message_context,
        )

        static_section = UserTemplateManager.build_static_section()
        user_message_text = processor.build_full_user_message(
            static_content=static_section,
            dynamic_content=dynamic_section,
            conversation_context=context_section.strip(),
        )

        if tool_history:
            history_block = _format_tool_history(tool_history)
            if history_block:
                user_message_text += f"\n\n{history_block}\n"

        if user_message:
            from ..graph_prompts import format_current_question
            user_message_text += format_current_question(user_message)

        if last_error:
            from ..error_injection import format_error_for_prompt
            user_message_text += "\n\n" + format_error_for_prompt(last_error)

        return context_section, user_message_text

    def generate_prompt(
        self,
        agent_type: str,
        mode: str,
        user_message: str,
        workspace_id: str,
        iteration_count: int,
        max_iterations: int,
        tool_schema_prompt: str,
        tool_history: List[dict],
        last_tool_result: Optional[str],
        todos: List[str],
        current_todo_index: int,
        plan_content: Optional[str] = None,
        parent_chain_messages: List[dict] = None,
        current_conversation_messages: List[dict] = None,
        last_error: Optional[Any] = None,
        message_context: Optional[dict] = None,
    ) -> Tuple[str, str, PromptStats]:
        """
        统一提示词生成入口。

        Returns:
            (system_prompt, user_message, PromptStats)
        """
        system_prompt = self.build_stable_system_prompt(agent_type, mode, tool_schema_prompt)
        stable_hash = self.stable_hash(agent_type, mode, tool_schema_prompt)

        context_section, user_message_text = self.build_user_message(
            user_message=user_message,
            workspace_id=workspace_id,
            iteration_count=iteration_count,
            max_iterations=max_iterations,
            tool_history=tool_history,
            todos=todos,
            current_todo_index=current_todo_index,
            plan_content=plan_content,
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            last_error=last_error,
            mode=mode,
            message_context=message_context,
        )

        stats = PromptStats(
            stable_chars=len(system_prompt),
            context_chars=len(context_section),
            volatile_chars=max(len(user_message_text) - len(context_section), 0),
            user_message_chars=len(user_message_text),
            total_chars=len(system_prompt) + len(user_message_text),
            stable_hash=stable_hash,
        )

        return system_prompt, user_message_text, stats

    @staticmethod
    def estimate_prompt_sizes(system_prompt: str, user_message_text: str) -> PromptStats:
        """不重建提示词，仅根据已生成的文本估算分层尺寸。"""
        stable_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        return PromptStats(
            stable_chars=len(system_prompt),
            context_chars=0,
            volatile_chars=len(user_message_text),
            user_message_chars=len(user_message_text),
            total_chars=len(system_prompt) + len(user_message_text),
            stable_hash=stable_hash,
        )
