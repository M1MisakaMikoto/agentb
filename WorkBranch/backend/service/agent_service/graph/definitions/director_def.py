from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta
from ...prompts.graph_prompts import DIRECT_SYSTEM_PROMPT


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
        try:
            from singleton import get_settings_service
            _settings = get_settings_service()
            _max_iter = int(_settings.get("agent:iterations:director:max"))
            _timeout = int(_settings.get("agent:tool_timeout_seconds"))
        except (KeyError, ValueError, ImportError):
            _max_iter = 32
            _timeout = 1200
        super().__init__(
            prompt=AgentPrompt(
                system_prompt=DIRECT_SYSTEM_PROMPT,
                mode="DIRECT",
            ),
            meta=AgentMeta(
                allowed_tools=[
                    "thinking",
                    "chat",
                    "call_explore_agent",
                    "call_review_agent",
                    "call_prediction_agent",
                    "call_plan_agent",
                    "list_workspace_files",
                    "get_workspace_info",
                    "search_files",
                    "read_file",
                    "write_file",
                    "delete_file",
                    "ask_user_question",
                    "update_todo",
                    "document",
                    "sql_query",
                    "rag_search",
                    "submit_ai_judgment_issue",
                    "submit_facility_report",
                    "submit_facility_forecast",
                    "submit_dailypatrol_record",
                ],
                default_tools=[
                    {"tool": "thinking", "args": {"description": ""}},
                    {"tool": "chat", "args": {"description": ""}},
                ],
                timeout_seconds=_timeout,
                max_iterations=_max_iter,  # 需要足够迭代完成预测+提交记录
                memory_mode="accumulate",
                agent_type="director_agent",
                is_subagent=False,
            ),
        )
