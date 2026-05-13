from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable


@dataclass
class AgentPrompt:
    """Agent 提示词定义
    
    包含 LLM 需要的所有提示词信息：
    - system_prompt: 系统级指导内容
    - mode: 执行模式（DIRECT/PLAN）
    """
    
    system_prompt: str
    mode: str = "DIRECT"  # DIRECT 或 PLAN
    
    def get_context_builder(self) -> Optional[Callable]:
        """获取上下文构建函数（可选）
        
        用于自定义如何构建发送给 LLM 的上下文。
        默认实现返回 None，表示使用标准 ReAct 格式。
        """
        return None


@dataclass
class AgentMeta:
    """Agent 元数据配置
    
    包含运行时参数和权限控制：
    - allowed_tools: 允许使用的工具列表
    - default_tools: 默认初始工具（通常是 thinking + chat）
    - timeout_seconds: 超时时间（秒）
    - max_iterations: 最大迭代次数
    - memory_mode: 记忆模式（accumulate/window）
    - agent_type: Agent 类型标识
    - is_subagent: 是否为子 Agent
    """
    
    allowed_tools: List[str]
    default_tools: List[Dict[str, Any]] = field(default_factory=list)
    timeout_seconds: int = 300
    max_iterations: int = 10
    memory_mode: str = "accumulate"  # accumulate 或 window
    agent_type: str = ""
    is_subagent: bool = True
    
    def get_allowed_tools(self) -> List[str]:
        """获取允许的工具列表"""
        return self.allowed_tools
    
    def get_default_tools(self, user_message: Any = None) -> List[Dict[str, Any]]:
        """获取默认工具列表
        
        Args:
            user_message: 用户消息（用于填充 thinking/chat 工具的 description 参数）
            
        Returns:
            默认工具列表，例如 [{"tool": "thinking", "args": {"description": user_message}}]
        """
        if not self.default_tools:
            return []
        
        result = []
        for tool_def in self.default_tools:
            tool_copy = tool_def.copy()
            if "args" in tool_copy and user_message is not None:
                args_copy = tool_copy["args"].copy()
                if "description" in args_copy:
                    args_copy["description"] = user_message
                tool_copy["args"] = args_copy
            result.append(tool_copy)
        
        return result


@dataclass
class AgentDefinition:
    """Agent 完整定义（模板方法模式）
    
    架构公式：
        ReActAgentBase + AgentDefinition = 具体Agent实例
        
    组成部分：
        - prompt: AgentPrompt（提示词定义）
        - meta: AgentMeta（元数据配置）
    
    使用示例：
        definition = AgentDefinition(
            prompt=DirectorPrompt(...),
            meta=DirectorMeta(...)
        )
        base = ReActAgentBase(definition=definition)
        result = base.execute(state)
    """
    
    prompt: AgentPrompt
    meta: AgentMeta
    
    def get_agent_type(self) -> str:
        """获取 Agent 类型标识"""
        return self.meta.agent_type
    
    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return self.prompt.system_prompt
    
    def get_execution_mode(self) -> str:
        """获取执行模式"""
        return self.prompt.mode
    
    def is_subagent(self) -> bool:
        """判断是否为子 Agent"""
        return self.meta.is_subagent


def create_director_definition() -> AgentDefinition:
    """工厂方法：创建 Director Agent 定义
    
    用于向后兼容，实际实现在 definitions/director_def.py 中
    """
    from .definitions.director_def import DirectorDefinition
    return DirectorDefinition()


def create_prediction_definition() -> AgentDefinition:
    """工厂方法：创建 Prediction Agent 定义"""
    from .definitions.prediction_def import PredictionDefinition
    return PredictionDefinition()


def create_explore_definition() -> AgentDefinition:
    """工厂方法：创建 Explore Agent 定义"""
    from .definitions.explore_def import ExploreDefinition
    return ExploreDefinition()


def create_review_definition() -> AgentDefinition:
    """工厂方法：创建 Review Agent 定义"""
    from .definitions.review_def import ReviewDefinition
    return ReviewDefinition()
