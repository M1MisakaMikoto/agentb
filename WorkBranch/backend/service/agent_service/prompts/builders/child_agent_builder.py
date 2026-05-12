"""
子Agent提示词组装器
负责子Agent（prediction_agent, explore_agent, review_agent）的提示词组装
"""

from typing import List, Optional, Tuple

from ..base.message_processor import MessageProcessor
from ..templates.system_prompts import SystemPromptManager


class ChildAgentPromptBuilder:
    """子Agent提示词组装器"""
    
    def __init__(self, settings_service=None):
        self.settings = settings_service
        self.processor = MessageProcessor(settings_service)
    
    def build_child_agent_prompt(
        self,
        agent_type: str,
        task_description: str,
        parent_chain_messages: List[dict] = None,
        current_conversation_messages: List[dict] = None,
        message_context: Optional[dict] = None,
    ) -> Tuple[str, str]:
        """
        构建子Agent的完整提示词
        
        Args:
            agent_type: 子agent类型 (prediction_agent, explore_agent, review_agent)
            task_description: 任务描述
            parent_chain_messages: 父链消息
            current_conversation_messages: 当前对话消息
            message_context: 消息上下文
        
        Returns:
            (system_prompt, user_message) 元组
        """
        # System Prompt（使用通用Director Prompt）
        system_prompt = SystemPromptManager.get_system_prompt(
            agent_type=agent_type,
            mode="DIRECT"
        )
        
        # 构建User Message
        # 包含：对话上下文 + 当前任务
        conversation_context = ""
        
        if parent_chain_messages:
            parent_ctx = self.processor.process_conversation_context(
                messages=parent_chain_messages,
                source_type="parent_chain",
                message_context=message_context
            )
            if parent_ctx:
                conversation_context += parent_ctx + "\n"
        
        if current_conversation_messages:
            current_ctx = self.processor.process_conversation_context(
                messages=current_conversation_messages,
                source_type="current_conversation",
                message_context=message_context
            )
            if current_ctx:
                conversation_context += current_ctx
        
        # 当前任务
        task_section = f"[当前任务]\n{task_description}"
        
        # 组装User Message
        parts = []
        if conversation_context.strip():
            parts.append(conversation_context.strip())
        parts.append(task_section)
        
        user_message = "\n\n".join(parts)
        
        return system_prompt, user_message
    
    def build_context_for_special_tool(
        self,
        tool_name: str,
        task_description: str,
        parent_chain_messages: List[dict],
        current_conversation_messages: List[dict],
    ) -> str:
        """
        为特殊工具（thinking/chat）构建上下文
        
        这个方法被SpecialToolPromptBuilder调用
        """
        from service.agent_service.prompts.graph_prompts import build_context_prompt
        
        full_prompt = build_context_prompt(
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            current_task=f"当前任务: {task_description}"
        )
        
        return full_prompt
