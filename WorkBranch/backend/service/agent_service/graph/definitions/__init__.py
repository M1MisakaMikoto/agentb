from .director_def import DirectorDefinition
from .prediction_def import PredictionDefinition
from .explore_def import ExploreDefinition
from .review_def import ReviewDefinition


AGENT_DEFINITIONS = {
    "director_agent": DirectorDefinition,
    "prediction_agent": PredictionDefinition,
    "explore_agent": ExploreDefinition,
    "review_agent": ReviewDefinition,
}


def get_definition(agent_type: str):
    """根据 agent_type 获取对应的 AgentDefinition 类
    
    Args:
        agent_type: Agent 类型标识（如 'director_agent', 'prediction_agent' 等）
        
    Returns:
        AgentDefinition 实例
        
    Raises:
        ValueError: 如果 agent_type 不存在
    """
    definition_class = AGENT_DEFINITIONS.get(agent_type)
    if not definition_class:
        available = ", ".join(AGENT_DEFINITIONS.keys())
        raise ValueError(
            f"未知的 Agent 类型: {agent_type}\n"
            f"可用的 Agent 类型: {available}"
        )
    
    return definition_class()


def list_available_agents() -> list:
    """列出所有可用的 Agent 类型"""
    return list(AGENT_DEFINITIONS.keys())


__all__ = [
    'DirectorDefinition',
    'PredictionDefinition',
    'ExploreDefinition',
    'ReviewDefinition',
    'AGENT_DEFINITIONS',
    'get_definition',
    'list_available_agents',
]
