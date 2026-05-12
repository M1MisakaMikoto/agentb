"""
Director Agent提示词组装器
负责主循环的提示词组装
"""

from typing import List, Optional, Tuple

from ..base.message_processor import MessageProcessor
from ..templates.system_prompts import SystemPromptManager
from ..templates.user_templates import UserTemplateManager
from ..templates.tool_schemas import ToolSchemaManager


class DirectorPromptBuilder:
    """Director Agent提示词组装器"""
    
    def __init__(self, settings_service=None):
        self.settings = settings_service
        self.processor = MessageProcessor(settings_service)
    
    def build_full_prompt(
        self,
        agent_type: str,
        mode: str,
        user_message: str,
        workspace_id: str,
        tool_schema_prompt: str,
        tool_history: List[dict],
        last_tool_result: Optional[str],
        todos: List[str],
        current_todo_index: int,
        plan_content: Optional[str] = None,
        parent_chain_messages: List[dict] = None,
        current_conversation_messages: List[dict] = None,
    ) -> Tuple[str, str]:
        """
        构建完整提示词对 (system_prompt, user_message)
        
        新结构顺序（按缓存优化）：
        1. System Prompt（静态）
        2. User Message:
           a. 工具列表 + 规则（静态）
           b. 元数据（低频）
           c. 工具执行记录（中频，去重合并）
           d. 对话上下文（高频，自动压缩）
        """
        
        # 1. 获取System Prompt
        system_prompt = SystemPromptManager.get_system_prompt(
            agent_type=agent_type,
            mode=mode,
            tool_prompt=tool_schema_prompt
        )
        
        # 2. 构建User Message各区块
        
        # 2a. 静态区域：工具列表 + 规则
        static_section = UserTemplateManager.build_static_section(tool_schema_prompt)
        
        # 2b. 低频元数据区域
        dynamic_section = self.processor.build_dynamic_section(
            user_message=user_message,
            workspace_id=workspace_id,
            todos=todos,
            current_todo_index=current_todo_index,
            plan_content=plan_content,
            include_iteration=False  # 已执行轮次默认不显示
        )
        
        # 2c. 工具执行记录（自动去重合并）
        tool_history_section = self.processor.process_tool_history(
            tool_history=tool_history,
            last_tool_result=last_tool_result
        )
        
        # 2d. 对话上下文（智能压缩）
        conversation_context = ""
        
        # 父链消息（如果有）
        if parent_chain_messages:
            parent_context = self.processor.process_conversation_context(
                messages=parent_chain_messages,
                source_type="parent_chain"
            )
            if parent_context:
                conversation_context += parent_context + "\n"
        
        # 当前对话消息（如果有）
        if current_conversation_messages:
            current_context = self.processor.process_conversation_context(
                messages=current_conversation_messages,
                source_type="current_conversation"
            )
            if current_context:
                conversation_context += current_context
        
        # 3. PLAN模式额外指令
        if mode.upper() == "PLAN":
            dynamic_section += "\n\n" + UserTemplateManager.build_plan_mode_suffix()
        
        # 4. 按优先级拼接User Message
        user_message_text = self.processor.build_full_user_message(
            static_content=static_section,
            dynamic_content=dynamic_section,
            tool_history_section=tool_history_section,
            conversation_context=conversation_context.strip()
        )
        
        return system_prompt, user_message_text
    
    def build_intent_analysis_prompt(
        self,
        user_message: str,
        parent_chain_messages: List[dict],
        current_conversation_messages: List[dict],
        tool_schema_prompt: str,
        message_context: Optional[dict] = None,
    ) -> Tuple[str, List[dict]]:
        """
        构建意图分析提示词
        """
        from ..prompts.graph_prompts import format_parent_chain_block, format_current_conversation_block, format_current_question
        
        system_prompt = SystemPromptManager.get_system_prompt(
            agent_type="director_agent",
            mode="INTENT_ANALYSIS",
            tool_prompt=tool_schema_prompt
        )
        
        prompt_parts = [
            format_parent_chain_block(parent_chain_messages, message_context),
            format_current_conversation_block(current_conversation_messages, message_context),
            format_current_question(user_message),
            "请分析以上用户当前问题的意图。"
        ]
        
        prompt = "\n".join(filter(None, prompt_parts))
        
        return system_prompt, [{"role": "user", "content": prompt}]
    
    def build_plan_generation_prompt(
        self,
        user_message: str,
        parent_chain_messages: List[dict],
        current_conversation_messages: List[dict],
        intent_analysis: Optional[dict] = None,
        settings_service=None,
        message_context: Optional[dict] = None,
    ) -> Tuple[str, List[dict]]:
        """
        构建计划生成提示词
        """
        from ..prompts.graph_prompts import (
            format_parent_chain_block, 
            format_current_conversation_block, 
            format_current_question,
            get_plan_system_prompt
        )
        
        system_prompt = get_plan_system_prompt("director_agent", settings_service)
        
        intent_context = ""
        if intent_analysis:
            intent_context = f"""
## 意图分析结果
- 意图类型: {intent_analysis.get('intent_type', 'unknown')}
- 需求摘要: {intent_analysis.get('summary', '')}
- 关键点: {', '.join(intent_analysis.get('key_points', []))}
- 建议工具: {', '.join(intent_analysis.get('suggested_tools', []))}
- 复杂度: {intent_analysis.get('complexity', 'medium')}
"""
        
        prompt_parts = [
            format_parent_chain_block(parent_chain_messages, message_context),
            format_current_conversation_block(current_conversation_messages, message_context),
            format_current_question(user_message),
            intent_context,
            "请根据以上用户当前问题生成执行计划，包含 2-5 个任务，严格按照 JSON 格式输出。"
        ]
        
        prompt = "\n".join(filter(None, prompt_parts))
        
        return system_prompt, [{"role": "user", "content": prompt}]
