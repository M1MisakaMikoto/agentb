from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta
from ...prompts.agent_prompts import EXPLORE_AGENT_PROMPT


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
            prompt=AgentPrompt(
                system_prompt=EXPLORE_AGENT_PROMPT,
                mode="DIRECT",
            ),
            meta=AgentMeta(
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
            ),
        )