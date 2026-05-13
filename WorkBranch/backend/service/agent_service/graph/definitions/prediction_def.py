from typing import List, Dict, Any

from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta


class PredictionPrompt(AgentPrompt):
    """Prediction Agent 的提示词定义"""
    
    def __init__(self):
        from ...prompts.agent_prompts import PREDICTION_AGENT_PROMPT
        
        super().__init__(
            system_prompt=PREDICTION_AGENT_PROMPT,
            mode="DIRECT",
        )


class PredictionMeta(AgentMeta):
    """Prediction Agent 的元数据配置
    
    配置说明：
    - 超时时间: 300s（统一标准）
    - 工具权限: 包含桥梁评估相关的所有工具
    - 默认工具: thinking + chat（标准配置）
    """
    
    def __init__(self):
        super().__init__(
            allowed_tools=[
                "thinking",
                "chat",
                "calculate_bci",
                "predict_trend",
                "query_standard",
                "read_document",
                "document",
                "list_workspace_files",
                "get_workspace_info",
                "search_files",
                "read_file",
            ],
            default_tools=[
                {"tool": "thinking", "args": {"description": ""}},
                {"tool": "chat", "args": {"description": ""}},
            ],
            timeout_seconds=300,
            max_iterations=10,
            memory_mode="accumulate",
            agent_type="prediction_agent",
            is_subagent=True,
        )


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
            prompt=PredictionPrompt(),
            meta=PredictionMeta(),
        )
    
    def get_agent_type(self) -> str:
        return "prediction_agent"
