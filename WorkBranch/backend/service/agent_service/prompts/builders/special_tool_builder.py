"""
特殊工具提示词组装器
负责thinking和chat工具的提示词组装
"""

from typing import List, Optional

from ..base.message_processor import MessageProcessor
from ..templates.system_prompts import SystemPromptManager


class SpecialToolPromptBuilder:
    """特殊工具提示词组装器"""
    
    def __init__(self, settings_service=None):
        self.settings = settings_service
        self.processor = MessageProcessor(settings_service)
    
    def build_thinking_prompt(
        self,
        task_description: str,
        parent_chain_messages: List[dict] = None,
        current_conversation_messages: List[dict] = None,
    ) -> tuple:
        """
        构建thinking工具的提示词
        
        Returns:
            (system_prompt, messages) 元组
            messages是list格式，用于LLM调用
        """
        # System Prompt
        system_prompt = SystemPromptManager.get_special_tool_prompt("thinking")
        
        # 构建上下文（使用压缩后的对话历史）
        from service.agent_service.prompts.graph_prompts import build_context_prompt
        
        full_context = build_context_prompt(
            parent_chain_messages=parent_chain_messages or [],
            current_conversation_messages=current_conversation_messages or [],
            current_task=f"请思考并执行当前任务: {task_description}"
        )
        
        messages = [{"role": "user", "content": full_context}]
        
        return system_prompt, messages
    
    def build_chat_prompt(
        self,
        task_description: str,
        previous_results: List[str] = None,
        parent_chain_messages: List[dict] = None,
    ) -> tuple:
        """
        构建chat工具的提示词
        
        Returns:
            (system_prompt, messages) 元组
        """
        # System Prompt
        system_prompt = SystemPromptManager.get_special_tool_prompt("chat")
        
        # 构建消息列表
        from service.agent_service.prompts.graph_prompts import build_special_tool_messages
        
        messages = build_special_tool_messages(
            task_description=task_description,
            previous_results=previous_results or [],
            final_instruction="请向用户输出回复。",
            parent_chain_messages=parent_chain_messages or []
        )
        
        return system_prompt, messages
    
    def build_special_tool_user_content(
        self,
        task_description: str,
        previous_results: List[str] = None,
    ) -> str:
        """
        构建特殊工具的用户消息内容
        
        用于日志记录等场景
        """
        from service.agent_service.prompts.graph_prompts import build_special_tool_prompt
        
        return build_special_tool_prompt(
            task_description=task_description,
            previous_results=previous_results or [],
            final_instruction=""
        )
