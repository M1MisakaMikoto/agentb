from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta
from ...prompts.agent_prompts import EXPLORE_AGENT_PROMPT


class ExploreDefinition(AgentDefinition):
    """Explore Agent 的完整定义

    架构公式：
        ReActAgentBase + ExploreDefinition = ExploreAgent

    专业领域：
    - 桥梁检测报告分析
    - 病害信息提取
    - 报告结构化处理

    特点：
    - 子 Agent（is_subagent=True）
    - 只读模式（不能修改文件）
    - 专门用于从检测报告中提取病害信息
    """

    def __init__(self):
        try:
            from singleton import get_settings_service
            _settings = get_settings_service()
            _max_iter = int(_settings.get("agent:iterations:explore:max"))
            _timeout = int(_settings.get("agent:subagent_timeout_seconds"))
        except (KeyError, ValueError, ImportError):
            _max_iter = 32
            _timeout = 1800
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
                    "document",
                    "analyze_image",
                    "read_file",
                ],
                default_tools=[
                    {"tool": "thinking", "args": {"description": ""}},
                    {"tool": "chat", "args": {"description": ""}},
                ],
                timeout_seconds=_timeout,
                max_iterations=_max_iter,
                memory_mode="accumulate",
                agent_type="explore_agent",
                is_subagent=True,
            ),
        )
