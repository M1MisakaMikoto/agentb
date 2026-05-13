from typing import List, Dict, Any

from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta


class DirectorPrompt(AgentPrompt):
    """Director Agent 的提示词定义"""
    
    def __init__(self):
        from ...prompts.graph_prompts import DIRECT_SYSTEM_PROMPT
        
        super().__init__(
            system_prompt=DIRECT_SYSTEM_PROMPT,
            mode="DIRECT",
        )


class DirectorMeta(AgentMeta):
    """Director Agent 的元数据配置"""
    
    def __init__(self):
        super().__init__(
            allowed_tools=[
                "thinking",
                "chat",
                "enter_plan_mode",
                "call_explore_agent",
                "call_review_agent",
                "call_prediction_agent",
                "list_workspace_files",
                "get_workspace_info",
                "search_files",
                "read_file",
                "write_file",
                "edit_file",
                "create_file",
                "delete_file",
                "run_command",
                "web_search",
                "web_fetch",
                "ask_user_question",
                "todo_write",
                "glob",
                "grep",
                "document",
            ],
            default_tools=[
                {"tool": "thinking", "args": {"description": ""}},
                {"tool": "chat", "args": {"description": ""}},
            ],
            timeout_seconds=300,
            max_iterations=15,
            memory_mode="accumulate",
            agent_type="director_agent",
            is_subagent=False,
        )


class DirectorDefinition(AgentDefinition):
    """Director Agent 的完整定义
    
    架构公式：
        ReActAgentBase + DirectorDefinition = DirectorAgent
    
    特点：
    - 主 Agent（is_subagent=False）
    - 拥有最完整的工具权限
    - 负责任务分发和协调
    """
    
    def __init__(self):
        super().__init__(
            prompt=DirectorPrompt(),
            meta=DirectorMeta(),
        )
    
    def get_agent_type(self) -> str:
        return "director_agent"
