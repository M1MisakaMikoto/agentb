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
        super().__init__(
            prompt=AgentPrompt(
                system_prompt=DIRECT_SYSTEM_PROMPT,
                mode="DIRECT",
            ),
            meta=AgentMeta(
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
                    "sql_query",
                    "rag_search",
                    "submit_ai_judgment_issue",
                    "submit_facility_report",
                    "submit_facility_forecast",
                ],
                default_tools=[
                    {"tool": "thinking", "args": {"description": ""}},
                    {"tool": "chat", "args": {"description": ""}},
                ],
                timeout_seconds=300,
                max_iterations=12,  # 需要足够迭代完成预测+提交记录
                memory_mode="accumulate",
                agent_type="director_agent",
                is_subagent=False,
            ),
        )