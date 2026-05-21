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
        super().__init__(
            prompt=AgentPrompt(
                system_prompt=PREDICTION_AGENT_PROMPT,
                mode="DIRECT",
            ),
            meta=AgentMeta(
                allowed_tools=[
                    "thinking",
                    "chat",
                    "calculate_bci",
                    "predict_trend",
                    "query_standard",
                    "document",
                    "list_workspace_files",
                    "get_workspace_info",
                    "search_files",
                    "read_file",
                ],
                default_tools=[
                    {"tool": "thinking", "args": {"description": ""}},
                    {"tool": "chat", "args": {"description": ""}},
                    {"tool": "list_workspace_files", "args": {"previous_results": []}},
                    {"tool": "document", "args": {}},
                ],
                timeout_seconds=300,
                max_iterations=10,
                memory_mode="accumulate",
                agent_type="prediction_agent",
                is_subagent=True,
            ),
        )