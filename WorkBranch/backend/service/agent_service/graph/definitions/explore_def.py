from typing import List, Dict, Any

from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta


class ExplorePrompt(AgentPrompt):
    """Explore Agent 的提示词定义"""
    
    def __init__(self):
        from ...prompts.agent_prompts import EXPLORE_AGENT_PROMPT
        
        super().__init__(
            system_prompt=EXPLORE_AGENT_PROMPT,
            mode="DIRECT",
        )


class ExploreMeta(AgentMeta):
    """Explore Agent 的元数据配置
    
    配置说明：
    - 超时时间: 300s（统一标准）
    - 工具权限: 只读工具为主
    - 默认工具: thinking + chat（标准配置）
    """
    
    def __init__(self):
        super().__init__(
            allowed_tools=[
                "thinking",
                "chat",
                "list_workspace_files",
                "get_workspace_info",
                "search_files",
                "read_file",
                "glob",
                "grep",
            ],
            default_tools=[
                {"tool": "thinking", "args": {"description": ""}},
                {"tool": "chat", "args": {"description": ""}},
            ],
            timeout_seconds=300,
            max_iterations=8,
            memory_mode="accumulate",
            agent_type="explore_agent",
            is_subagent=True,
        )


class ExploreDefinition(AgentDefinition):
    """Explore Agent 的完整定义
    
    架构公式：
        ReActAgentBase + ExploreDefinition = ExploreAgent
    
    专业领域：
    - 代码探索与理解
    - 项目结构分析
    - 代码依赖追踪
    
    特点：
    - 子 Agent（is_subagent=True）
    - 只读模式（不能修改文件）
    - 快速搜索和定位能力
    """
    
    def __init__(self):
        super().__init__(
            prompt=ExplorePrompt(),
            meta=ExploreMeta(),
        )
    
    def get_agent_type(self) -> str:
        return "explore_agent"
