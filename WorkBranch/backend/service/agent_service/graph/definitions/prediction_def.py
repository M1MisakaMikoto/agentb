from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta
from ...prompts.agent_prompts import PREDICTION_AGENT_PROMPT


class PredictionDefinition(AgentDefinition):
    """Prediction Agent 的完整定义

    架构公式：
        ReActAgentBase + PredictionDefinition = PredictionAgent

    专业领域：
    - 桥梁技术状况评估与预测
    - BCI 计算与分析
    - 趋势预测
    - 规范查询

    特点：
    - 子 Agent（is_subagent=True）
    - 拥有桥梁分析专用工具
    - 需要完整的短期记忆支持
    """

    def __init__(self):
        try:
            from singleton import get_settings_service
            _max_iter = int(get_settings_service().get("agent:iterations:prediction:max"))
        except (KeyError, ValueError, ImportError):
            _max_iter = 10
        super().__init__(
            prompt=AgentPrompt(
                system_prompt=PREDICTION_AGENT_PROMPT,
                mode="DIRECT",
            ),
            meta=AgentMeta(
                allowed_tools=[
                    "thinking",
                    "chat",
                    "bridge_report_parser",
                    "calculate_bci",
                    "predict_trend",
                    "query_standard",
                    "document",
                    "list_workspace_files",
                    "get_workspace_info",
                    "search_files",
                    "read_file",
                    "submit_facility_report",
                    "submit_facility_forecast",
                ],
                default_tools=[
                    {"tool": "thinking", "args": {"description": ""}},
                    {"tool": "chat", "args": {"description": ""}},
                ],
                timeout_seconds=300,
                max_iterations=_max_iter,
                memory_mode="accumulate",
                agent_type="prediction_agent",
                is_subagent=True,
            ),
        )