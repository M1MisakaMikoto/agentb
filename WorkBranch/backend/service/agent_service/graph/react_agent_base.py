import datetime
from typing import Dict, Any, Optional, Callable, List

from .agent_definition import AgentDefinition

from ..state import AgentState


class MemoryManager:
    """记忆管理组件
    
    负责管理和格式化 Agent 的短期记忆 (tool_history)
    
    核心功能：
    - 从 state.tool_history 提取历史记录
    - 格式化为 previous_results 列表
    - 注入到工具参数中（修复记忆断裂问题）
    """
    
    @staticmethod
    def extract_tool_history(state: AgentState) -> List[Dict[str, Any]]:
        """从 state 中提取 tool_history"""
        return state.get('tool_history', []) or []
    
    @staticmethod
    def format_previous_results(tool_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将 tool_history 格式化为 previous_results 列表
        
        Args:
            tool_history: 历史工具执行记录列表
            
        Returns:
            格式化后的 previous_results 列表
            每个元素包含 {tool_name, result} 结构
        """
        if not tool_history:
            return []
        
        results = []
        for record in tool_history:
            if isinstance(record, dict):
                formatted_record = {
                    "tool_name": record.get("tool_name", "unknown"),
                    "result": record.get("result", ""),
                    "timestamp": record.get("timestamp", datetime.datetime.now().isoformat()),
                }
                
                if record.get("error"):
                    formatted_record["error"] = record["error"]
                
                results.append(formatted_record)
        
        return results
    
    @staticmethod
    def inject_memory(
        tool_args: Dict[str, Any],
        state: AgentState,
        memory_mode: str = "accumulate"
    ) -> Dict[str, Any]:
        """将记忆注入到工具参数中 🔧 关键修复！
        
        这是修复 REQ-1（Subagent 记忆机制断裂）的核心方法。
        
        Args:
            tool_args: 原始工具参数
            state: Agent 当前状态
            memory_mode: 记忆模式 ('accumulate' 或 'window')
            
        Returns:
            增强后的工具参数（包含 previous_results）
        """
        enhanced_args = tool_args.copy() if tool_args else {}
        
        tool_history = MemoryManager.extract_tool_history(state)
        
        if not tool_history:
            enhanced_args["previous_results"] = []
            return enhanced_args
        
        if memory_mode == "window":
            window_size = 10
            tool_history = tool_history[-window_size:]
        
        previous_results = MemoryManager.format_previous_results(tool_history)
        enhanced_args["previous_results"] = previous_results
        
        return enhanced_args


class LoopController:
    """循环控制组件
    
    负责：
    - 检测死循环（DoomLoop）
    - 控制最大迭代次数
    - 决定是否继续执行
    """
    
    def __init__(self, max_iterations: int = 10):
        self.max_iterations = max_iterations
        self._iteration_count = 0
        self._recent_tools = []  # 用于检测重复模式
    
    def check_loop_condition(self, state: AgentState) -> bool:
        """检查是否应该继续循环
        
        Returns:
            True 表示可以继续，False 表示应该停止
        """
        self._iteration_count += 1
        
        if self._iteration_count > self.max_iterations:
            return False
        
        pending_tools = state.get('pending_tools', [])
        if not pending_tools or len(pending_tools) == 0:
            return False
        
        current_tool = pending_tools[0] if pending_tools else None
        if current_tool:
            tool_name = current_tool.get("tool", "") if isinstance(current_tool, dict) else str(current_tool)
            self._recent_tools.append(tool_name)
            
            if len(self._recent_tools) > 20:
                self._recent_tools = self._recent_tools[-20:]
            
            if self._detect_doom_loop():
                return False
        
        return True
    
    def _detect_doom_loop(self) -> bool:
        """检测死循环模式
        
        简单实现：检查最近 N 个工具是否有重复模式
        更复杂的实现可以使用序列匹配算法
        """
        if len(self._recent_tools) < 4:
            return False
        
        recent_4 = self._recent_tools[-4:]
        if len(set(recent_4)) == 1 and recent_4[0] in ["thinking", "chat"]:
            return True
        
        return False
    
    def get_iteration_count(self) -> int:
        return self._iteration_count
    
    def reset(self):
        self._iteration_count = 0
        self._recent_tools = []


class ReActAgentBase:
    """ReAct 编排基础类（模板方法模式 + 策略模式）
    
    架构公式：
        ReActAgentBase + AgentDefinition = 具体Agent实例
    
    核心职责：
    1. 统一的记忆管理（通过 MemoryManager）
    2. 统一的工具执行（包括特殊工具 thinking/chat）
    3. 循环控制（通过 LoopController）
    4. 状态更新和维护
    
    策略模式支持：
    - chat_strategy: Chat 工具的执行策略（可覆盖）
    - thinking_strategy: Thinking 工具的执行策略（可覆盖）
    
    使用示例：
        definition = DirectorDefinition()
        base = ReActAgentBase(definition=definition)
        
        # 覆盖 Director Agent 的特殊策略
        base.chat_strategy = director_chat_strategy
        base.thinking_strategy = director_thinking_strategy
        
        result = base.execute(state)
    """
    
    SPECIAL_TOOLS = {"thinking", "chat"}
    
    def __init__(self, definition: AgentDefinition):
        self.definition = definition
        self.memory_manager = MemoryManager()
        self.loop_controller = LoopController(
            max_iterations=definition.meta.max_iterations
        )
        
        # 策略模式：默认使用 tool_executor 的实现
        # 可被外部覆盖以支持不同 Agent 的特殊行为
        self._chat_strategy = None  # 延迟初始化
        self._thinking_strategy = None  # 延迟初始化
    
    @property
    def chat_strategy(self):
        """Chat 工具执行策略（可调用对象）"""
        if self._chat_strategy is None:
            from .subgraphs.tool_executor import _execute_chat_tool
            self._chat_strategy = _execute_chat_tool
        return self._chat_strategy
    
    @chat_strategy.setter
    def chat_strategy(self, value):
        self._chat_strategy = value
    
    @property
    def thinking_strategy(self):
        """Thinking 工具执行策略（可调用对象）"""
        if self._thinking_strategy is None:
            from .subgraphs.tool_executor import _execute_thinking_tool
            self._thinking_strategy = _execute_thinking_tool
        return self._thinking_strategy
    
    @thinking_strategy.setter
    def thinking_strategy(self, value):
        self._thinking_strategy = value
    
    def execute(self, state: AgentState) -> Dict[str, Any]:
        """执行 Agent 的主循环（模板方法）
        
        这是 ReAct 模式的核心算法骨架：
        1. 获取当前待执行工具
        2. 注入记忆到工具参数
        3. 执行工具（特殊或普通）
        4. 更新状态
        5. 检查循环条件
        
        Args:
            state: Agent 当前状态
            
        Returns:
            执行结果字典
        """
        pending_tools = state.get('pending_tools', [])
        
        if not pending_tools:
            return {
                "status": "completed",
                "message": "没有待执行的工具",
                "final_reply": state.get('final_reply'),
            }
        
        current_tool = pending_tools[0]
        tool_name = current_tool.get("tool", "") if isinstance(current_tool, dict) else str(current_tool)
        tool_args = current_tool.get("args", {}) if isinstance(current_tool, dict) else {}
        
        task_description = state.get('task_description', '')
        
        print(f"[ReActAgentBase] 执行工具: {tool_name}")
        print(f"[ReActAgentBase] 任务描述: {task_description[:100]}...")
        
        enhanced_args = self.memory_manager.inject_memory(
            tool_args=tool_args,
            state=state,
            memory_mode=self.definition.meta.memory_mode
        )
        
        has_memory_injected = len(enhanced_args.get("previous_results", [])) > 0
        if has_memory_injected:
            print(f"[ReActAgentBase] ✅ 已注入 {len(enhanced_args['previous_results'])} 条历史记录")
        
        try:
            if tool_name in self.SPECIAL_TOOLS:
                result = self._execute_special_tool(
                    tool_name=tool_name,
                    tool_args=enhanced_args,
                    task_description=task_description,
                    state=state,
                )
            else:
                result = self._execute_normal_tool(
                    tool_name=tool_name,
                    tool_args=enhanced_args,
                    state=state,
                )
            
            updated_state = self._update_state(
                state=state,
                tool_name=tool_name,
                result=result,
                enhanced_args=enhanced_args,
            )
            
            should_continue = self.loop_controller.check_loop_condition(updated_state)
            
            return {
                "status": "continue" if should_continue else "completed",
                "result": result,
                "updated_state": updated_state,
                "tool_executed": tool_name,
                "memory_injected": has_memory_injected,
                "iteration_count": self.loop_controller.get_iteration_count(),
            }
            
        except Exception as e:
            error_msg = f"工具执行失败 [{tool_name}]: {e}"
            print(f"[ReActAgentBase] ❌ {error_msg}")
            return {
                "status": "error",
                "error": str(e),
                "message": error_msg,
                "tool_executed": tool_name,
            }
    
    def execute_child_step(
        self,
        state: AgentState,
        llm_service=None,
        message_context: dict = None,
    ) -> Dict[str, Any]:
        """执行子 Agent 的一步操作（简化接口）
        
        专用于子 Agent 图节点的便捷方法。
        
        Args:
            state: 子 Agent 状态
            llm_service: LLM 服务实例
            message_context: 消息上下文
            
        Returns:
            执行结果字典
        """
        from .subgraphs.tool_executor import run_tool_execution
        
        pending_tools = state.get('pending_tools', [])
        if not pending_tools:
            return {
                "final_reply": state.get('final_reply'),
                "pending_tools": [],
                "tool_history": state.get('tool_history', []),
            }
        
        current_tool = pending_tools[0]
        tool_name = current_tool.get("tool", "") if isinstance(current_tool, dict) else str(current_tool)
        original_args = current_tool.get("args", {}) if isinstance(current_tool, dict) else {}
        
        task_description = state.get('task_description', '')
        
        enhanced_args = self.memory_manager.inject_memory(
            tool_args=original_args,
            state=state,
            memory_mode=self.definition.meta.memory_mode
        )
        
        print(f"[ReActAgentBase.execute_child_step] 工具: {tool_name}")
        print(f"[ReActAgentBase.execute_child_step] 记忆注入: {len(enhanced_args.get('previous_results', []))} 条")
        
        execution_result = run_tool_execution(
            tool_name=tool_name,
            tool_args=enhanced_args,
            workspace_id=state.get('workspace_id'),
            task_description=task_description,
            llm_service=llm_service,
            message_context=message_context,
        )
        
        new_tool_history = list(state.get('tool_history', []) or [])
        history_entry = {
            "tool_name": tool_name,
            "args": enhanced_args,
            "result": execution_result.get("result"),
            "error": execution_result.get("error"),
            "timestamp": datetime.datetime.now().isoformat(),
        }
        new_tool_history.append(history_entry)
        
        remaining_tools = pending_tools[1:] if len(pending_tools) > 1 else []
        
        final_reply = None
        if tool_name == "chat" and not execution_result.get("error"):
            final_reply = execution_result.get("result")
        
        return {
            "final_reply": final_reply,
            "pending_tools": remaining_tools,
            "tool_history": new_tool_history,
            "last_execution": {
                "tool_name": tool_name,
                "result": execution_result,
                "memory_was_injected": len(enhanced_args.get('previous_results', [])) > 0,
            },
        }
    
    def _execute_special_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        task_description: str,
        state: AgentState,
    ) -> Dict[str, Any]:
        """执行特殊工具（thinking / chat）- 使用策略模式
        
        策略模式设计：
        - 默认策略：使用 tool_executor 的标准实现（适用于子Agent）
        - 可覆盖策略：Director Agent 可注入特殊实现（支持多模态等）
        
        Args:
            tool_name: 工具名称 (thinking/chat)
            tool_args: 增强后的参数（包含 previous_results）
            task_description: 任务描述
            state: Agent 当前状态
            
        Returns:
            工具执行结果
        """
        from .subgraphs.tool_executor import SPECIAL_TOOLS_CONFIG
        
        llm_service = state.get('llm_service')
        message_context = state.get('message_context', {})
        
        config = SPECIAL_TOOLS_CONFIG.get(tool_name, {})
        
        print(f"[ReActAgentBase] 执行特殊工具: {tool_name} (使用策略模式)")
        print(f"[ReActAgentBase] 参数中的 previous_results 数量: {len(tool_args.get('previous_results', []))}")
        
        if tool_name == "thinking":
            next_task = (
                tool_args.get("next_task") 
                or tool_args.get("description") 
                or task_description
            )
            adjusted_args = {**tool_args, "next_task": next_task}
            
            # 使用策略模式：调用 self.thinking_strategy
            return self.thinking_strategy(
                tool_name=tool_name,
                tool_args=adjusted_args,
                task_description=next_task,
                llm_service=llm_service,
                message_context=message_context,
                config=config,
            )
        
        elif tool_name == "chat":
            # 使用策略模式：调用 self.chat_strategy
            return self.chat_strategy(
                tool_name=tool_name,
                tool_args=tool_args,
                task_description=task_description,
                llm_service=llm_service,
                message_context=message_context,
                config=config,
            )
        
        else:
            return {
                "result": f"未知特殊工具: {tool_name}",
                "error": f"Unknown special tool: {tool_name}",
            }
    
    def _execute_normal_tool(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        state: AgentState,
    ) -> Dict[str, Any]:
        """执行普通工具
        
        委托给 tool_executor 的通用执行逻辑
        """
        from .subgraphs.tool_executor import run_tool_execution
        
        task_description = state.get('task_description', '')
        llm_service = state.get('llm_service')
        message_context = state.get('message_context', {})
        
        return run_tool_execution(
            tool_name=tool_name,
            tool_args=tool_args,
            workspace_id=state.get('workspace_id'),
            task_description=task_description,
            llm_service=llm_service,
            message_context=message_context,
        )
    
    def _update_state(
        self,
        state: AgentState,
        tool_name: str,
        result: Dict[str, Any],
        enhanced_args: Dict[str, Any],
    ) -> AgentState:
        """更新 Agent 状态
        
        将执行结果记录到 tool_history 中
        """
        new_tool_history = list(state.get('tool_history', []) or [])
        
        history_entry = {
            "tool_name": tool_name,
            "args": enhanced_args,
            "result": result.get("result"),
            "error": result.get("error"),
            "timestamp": datetime.datetime.now().isoformat(),
            "had_memory": len(enhanced_args.get('previous_results', [])) > 0,
        }
        
        new_tool_history.append(history_entry)
        
        state['tool_history'] = new_tool_history
        
        remaining_tools = list(state.get('pending_tools', []) or [])
        if remaining_tools and len(remaining_tools) > 0:
            remaining_tools = remaining_tools[1:]
        state['pending_tools'] = remaining_tools
        
        if tool_name == "chat" and not result.get("error"):
            state['final_reply'] = result.get("result")
        
        return state
    
    def create_graph(self):
        """创建 LangGraph StateGraph（可选）
        
        如果需要将 ReActAgentBase 集成到 LangGraph 中，
        可以重写此方法返回一个完整的图。
        """
        from langgraph.graph import StateGraph, END
        
        graph = StateGraph(AgentState)
        
        def execute_node(state: AgentState) -> dict:
            result = self.execute(state)
            if result.get("updated_state"):
                return result["updated_state"].__dict__
            return {}
        
        graph.add_node("execute", execute_node)
        graph.set_entry_point("execute")
        graph.add_conditional_edges(
            "execute",
            lambda s: "end" if not getattr(s, 'pending_tools', None) else "execute",
            {"end": END, "execute": "execute"},
        )
        
        return graph.compile()


def create_react_agent(agent_type: str) -> ReActAgentBase:
    """工厂函数：根据 agent_type 创建 ReActAgentBase 实例
    
    Args:
        agent_type: Agent 类型标识（如 'director_agent', 'prediction_agent' 等）
        
    Returns:
        配置好的 ReActAgentBase 实例
    """
    from .agent_definition import (
        create_director_definition,
        create_prediction_definition,
        create_explore_definition,
        create_review_definition,
    )
    
    factory_map = {
        "director_agent": create_director_definition,
        "prediction_agent": create_prediction_definition,
        "explore_agent": create_explore_definition,
        "review_agent": create_review_definition,
    }
    
    factory_func = factory_map.get(agent_type)
    if not factory_func:
        raise ValueError(f"未知的 Agent 类型: {agent_type}")
    
    definition = factory_func()
    return ReActAgentBase(definition=definition)
