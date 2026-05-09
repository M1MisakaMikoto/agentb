"""
Agent Graph Configuration - 代理图配置

包含所有代理类型的配置信息，避免循环导入
"""

from typing import Optional
from .decision.complexity_analyzer import ExecutionMode


AGENT_GRAPH_CONFIG = {
    "director_agent": {
        "execution_mode": None,
    },
    "explore_agent": {
        "execution_mode": ExecutionMode.DIRECT,
        "system_prompt_key": "EXPLORE",
    },
    "review_agent": {
        "execution_mode": ExecutionMode.DIRECT,
        "system_prompt_key": "REVIEW",
    },
    "prediction_agent": {
        "execution_mode": ExecutionMode.DIRECT,
        "system_prompt_key": "PREDICTION",
    },
}