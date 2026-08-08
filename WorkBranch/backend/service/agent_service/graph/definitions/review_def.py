from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta
from ...prompts.agent_prompts import REVIEW_AGENT_PROMPT


class ReviewDefinition(AgentDefinition):
    """Review Agent 的完整定义

    架构公式：
        ReActAgentBase + ReviewDefinition = ReviewAgent

    专业领域：
    - 代码审查与质量分析
    - 问题发现与优化建议
    - 安全漏洞检测

    特点：
    - 子 Agent（is_subagent=True）
    - 只读模式（不能修改文件）
    - 专注于代码质量评估
    """

    def __init__(self):
        try:
            from singleton import get_settings_service
            _settings = get_settings_service()
            _max_iter = int(_settings.get("agent:iterations:review:max"))
            _timeout = int(_settings.get("agent:subagent_timeout_seconds"))
        except (KeyError, ValueError, ImportError):
            _max_iter = 32
            _timeout = 1800
        super().__init__(
            prompt=AgentPrompt(
                system_prompt=REVIEW_AGENT_PROMPT,
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
                    "sql_query",
                ],
                default_tools=[
                    {"tool": "thinking", "args": {"description": ""}},
                    {"tool": "chat", "args": {"description": ""}},
                ],
                timeout_seconds=_timeout,
                max_iterations=_max_iter,
                memory_mode="accumulate",
                agent_type="review_agent",
                is_subagent=True,
            ),
        )
