import datetime
from typing import Dict, Any, Optional, Callable, List

from .agent_definition import AgentDefinition
from core.logging import console
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
                    "reason": record.get("reason", ""),  # 工具调用原因
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
                    task_description=reason,
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
            task_description=reason,
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
                task_description=reason,
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
            task_description=reason,
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
    
    def _create_error_summary_node(self, llm_service=None, message_context=None):
        def error_summary_node(state: AgentState) -> dict:
            error_type = state.get("error_summary_type", "unknown")
            tool_history = state.get("tool_history", []) or []
            iteration_count = state.get("iteration_count", 0) or 0
            max_iterations = state.get("max_iterations", 10) or 10
            decision_error_count = state.get("decision_error_count", 0) or 0
            user_message = state.get("user_message") or ""
            last_tool_name = state.get("last_tool_name")
            last_tool_result = state.get("last_tool_result") or ""
            pending_tools = state.get("pending_tools") or []

            # ===== 输出详细的错误原因 =====
            console.warning("=" * 60)
            console.warning(f"[error_summary] ⚠️ 进入错误总结节点")
            console.warning(f"  错误类型: {error_type}")
            console.warning(f"  迭代次数: {iteration_count}/{max_iterations}")
            console.warning(f"  决策错误次数: {decision_error_count}")
            console.warning(f"  待执行工具: {len(pending_tools)} 个")
            console.warning(f"  工具历史: {len(tool_history)} 条")
            if last_tool_name:
                console.warning(f"  最后执行工具: {last_tool_name}")
                console.warning(f"  最后工具结果: {str(last_tool_result)[:200]}...")
            console.warning("=" * 60)

            # 构建详细的状态摘要
            state_summary = f"""
## 错误摘要
- 错误类型: {error_type}
- 触发原因: {
    "迭代次数超限" if error_type == "max_iterations" else
    "决策连续失败" if error_type == "decision_repeated_failure" else
    "检测到循环/卡死" if "loop" in str(error_type).lower() else
    error_type
}
- 迭代次数: {iteration_count}/{max_iterations}
- 决策错误次数: {decision_error_count}
- 待执行工具数量: {len(pending_tools)}
"""

            summary_prompt = f"""【任务执行异常终止 - 需要总结报告】

错误类型: {error_type}
执行轮次: {iteration_count}/{max_iterations}
原始任务: {user_message}
{state_summary}

【工具调用历史】(共{len(tool_history)}次)
"""
            for idx, item in enumerate(tool_history[-15:], 1):
                tool_name = item.get("tool_name", "unknown")
                result_preview = str(item.get("result", ""))[:200]
                error_info = " [失败]" if item.get("error") else ""
                summary_prompt += f"\n{idx}. {tool_name}{error_info}: {result_preview}...\n"

            summary_prompt += """
【要求】
请基于以上完整的任务执行历史，生成一份面向用户的总结报告，包括：
1. 任务目标是什么
2. 执行过程中做了哪些操作（按时间顺序）
3. 在哪个环节遇到问题，具体是什么问题
4. 已经获取了哪些信息/完成了哪些部分工作
5. 对后续处理的建议

请用中文输出，语气专业但易懂。直接输出总结内容，不要添加额外格式。"""

            if llm_service:
                try:
                    response = llm_service.chat(
                        messages=[{"role": "user", "content": summary_prompt}],
                        system_prompt="你是任务执行分析专家。当任务因循环、超时或异常而被迫终止时，你需要基于完整执行历史生成有意义的总结报告。",
                    )
                    reply = response.strip() if response else f"任务因 [{error_type}] 终止，已执行 {iteration_count} 轮"
                except Exception as e:
                    reply = f"任务因 [{error_type}] 终止，已执行 {iteration_count} 轮。(总结生成失败: {e})"
            else:
                reply = f"任务因 [{error_type}] 终止，已执行 {iteration_count} 轮，共调用工具 {len(tool_history)} 次。"

            console.warning(f"[error_summary] 生成总结: {reply[:200]}...")

            return {
                "final_reply": reply,
                "has_tool_use": False,
                "pending_tools": [],
                "force_error_summary": False,
            }

        return error_summary_node
    
    def build_react_loop_graph(self, config: dict = None):
        from langgraph.graph import StateGraph, END
        
        config = config or {}
        enable_todo = config.get("enable_todo", False)
        post_execute_hook = config.get("post_execute_hook", None)
        llm_service = config.get("llm_service")
        settings_service = config.get("settings_service")
        message_context = config.get("message_context")
        
        loop_graph = StateGraph(AgentState)
        
        decide_node = self._create_decide_node(
            llm_service=llm_service,
            settings_service=settings_service,
            message_context=message_context,
        )
        execute_node = self._create_execute_node(
            llm_service=llm_service,
            settings_service=settings_service,
            message_context=message_context,
            post_execute_hook=post_execute_hook,
        )
        
        loop_graph.add_node("decide", decide_node)
        loop_graph.add_node("execute", execute_node)
        loop_graph.add_node("error_summary", self._create_error_summary_node(llm_service=llm_service))
        
        if enable_todo:
            todo_review_node = self._create_todo_review_node(
                llm_service=llm_service,
                message_context=message_context,
            )
            loop_graph.add_node("todo_review", todo_review_node)
        
        loop_graph.set_entry_point("decide")
        
        loop_graph.add_conditional_edges(
            "decide",
            self._route_after_decide,
            {
                "execute": "execute",
                "done": END,
            }
        )
        
        execute_routes = {"decide": "decide", "execute": "execute", "done": END, "error_summary": "error_summary"}
        if enable_todo:
            execute_routes["todo_review"] = "todo_review"

        loop_graph.add_conditional_edges(
            "execute",
            self._route_after_execute,
            execute_routes
        )

        if enable_todo:
            loop_graph.add_conditional_edges(
                "todo_review",
                lambda s: "decide",
                {"decide": "decide"}
            )

        loop_graph.add_edge("error_summary", END)

        return loop_graph.compile()

    def _route_after_execute(self, state: AgentState) -> str:
        """路由 after_execute"""
        # ===== 防止 pending_tools 为空时进入未知状态 =====
        if not state.get("pending_tools"):
            console.warning("[_route_after_execute] ⚠️ pending_tools 为空，正常流程应设置 final_reply 或进入 todo_review")

        if state.get("final_reply"):
            return "done"

        if state.get("force_error_summary"):
            return "error_summary"

        # ===== 【关键修复】前置迭代次数检查，防止 LangGraph 递归限制 =====
        iteration_count = state.get("iteration_count", 0) or 0
        max_iterations = state.get("max_iterations", 10) or 10
        if iteration_count >= max_iterations:
            console.warning(f"[_route_after_execute] 迭代次数已达上限 ({iteration_count}/{max_iterations})")
            return "error_summary"

        next_action = state.get("next_action") or {}
        if next_action.get("kind") == "enter_plan":
            return "done"

        if state.get("pending_tools"):
            return "execute"

        if state.get("force_error_summary"):
            return "error_summary"

        # ===== 新增：检测工具失败循环 =====
        # 当工具持续失败且没有进展时，强制结束循环
        iteration_count = state.get("iteration_count", 0) or 0
        last_tool_success = state.get("last_tool_success")
        last_tool_name = state.get("last_tool_name")
        last_tool_result = state.get("last_tool_result") or ""
        tool_history = state.get("tool_history", []) or []

        # 基础保护：如果迭代次数过多（超过15次）且没有pending_tools，强制终止
        if iteration_count > 15 and not state.get("pending_tools"):
            console.warning(f"[_route_after_execute] 迭代次数过多 ({iteration_count})")
            return "error_summary"

        # ===== 新增：检测 document 工具读取空内容循环 =====
        if last_tool_name == "document" and last_tool_success is not False:
            # document 工具没有报错，但检查是否返回空内容
            result_str = str(last_tool_result) if last_tool_result else ""
            is_empty_result = len(result_str) < 100  # 结果很短

            if is_empty_result:
                # 检查最近几次 document 读取是否都是空的
                recent_empty_reads = 0
                for item in reversed(tool_history[-6:]):
                    if item.get("tool_name") == "document":
                        result = str(item.get("result", "") or "")
                        if len(result) < 100:
                            recent_empty_reads += 1

                # 如果连续 3 次 document 读取都返回空内容，强制终止
                if recent_empty_reads >= 3:
                    console.warning(f"[_route_after_execute] document 工具连续 {recent_empty_reads} 次返回空内容")
                    return "error_summary"
        # ===== 结束新增 =====

        if last_tool_success is False and last_tool_name:
            # 检查最近是否有连续失败
            recent_failures = 0
            repeated_same_tool = 0
            last_failed_args = None

            for item in reversed(tool_history[-10:]):
                if item.get("tool_name") == last_tool_name:
                    if item.get("result") is None or item.get("error"):
                        recent_failures += 1
                        current_args = item.get("args", {})
                        if last_failed_args == current_args:
                            repeated_same_tool += 1
                        last_failed_args = current_args

            # 如果同一工具连续失败超过3次，且参数相同，强制报告问题
            if repeated_same_tool >= 3:
                console.warning(f"[_route_after_execute] 工具 {last_tool_name} 连续失败 {repeated_same_tool} 次")
                return "error_summary"

            # 如果最近5次工具有4次以上失败，强制报告问题
            if recent_failures >= 4 and len(tool_history) >= 5:
                console.warning(f"[_route_after_execute] 最近5次工具中有 {recent_failures} 次失败")
                return "error_summary"
        # ===== 结束新增 =====

        return "decide"
    
    def _create_decide_node(self, llm_service=None, settings_service=None, message_context=None):
        from service.agent_service.prompts.graph_prompts import generate_prompt
        from .subgraphs.tool_registry import get_allowed_tools, is_tool_allowed
        import json
        
        def decide_tool_action_node(state: AgentState) -> dict:
            _messages = state.get("messages") or []
            _last_msg = _messages[-1] if _messages else None
            user_message = state.get("user_message") or (
                _last_msg.get("content", "") if isinstance(_last_msg, dict) else str(_last_msg) if _last_msg else ""
            )
            
            agent_type = state.get("agent_type") or "director_agent"
            tool_history = state.get("tool_history", []) or []
            last_tool_result = state.get("last_tool_result")
            iteration_count = state.get("iteration_count", 0) or 0
            max_iterations = state.get("max_iterations", 10) or 10
            
            if iteration_count >= max_iterations:
                return {
                    "force_error_summary": True,
                    "error_summary_type": "max_iterations",
                    "pending_tools": [],
                    "iteration_count": iteration_count,
                }

            # ===== 决策错误保护 =====
            # 当决策连续失败时，防止无限循环重试，直接终止
            decision_error_count = state.get("decision_error_count", 0) or 0
            if decision_error_count >= 3:
                return {
                    "force_error_summary": True,
                    "error_summary_type": "decision_repeated_failure",
                    "pending_tools": [],
                    "iteration_count": iteration_count,
                }

            if iteration_count > 0 and iteration_count % 8 == 0:
                from .director_agent import _check_loop_or_stuck
                check_result = _check_loop_or_stuck(
                    tool_history, 
                    iteration_count, 
                    llm_service,
                    user_message=user_message,
                )
                if check_result.get("action") == "stop":
                    return {
                        "force_error_summary": True,
                        "error_summary_type": check_result.get("reason", "loop_or_stuck"),
                        "pending_tools": [],
                        "iteration_count": iteration_count,
                    }
            
            if llm_service is None:
                reply = f"无法为任务自动决策下一步"
                return {
                    "next_action": {"kind": "reply", "reply": reply},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                }
            
            allowed_tools = get_allowed_tools(agent_type, settings_service)
            from service.agent_service.prompts.graph_prompts import build_tool_schema_prompt as _build_tool_schema_prompt
            tool_schema_prompt = _build_tool_schema_prompt(allowed_tools, agent_type=agent_type)
            
            system_prompt, context_prompt = generate_prompt(
                agent_type=agent_type,
                mode="DIRECT",
                user_message=user_message,
                workspace_id=state.get('workspace_id', ''),
                iteration_count=iteration_count,
                max_iterations=max_iterations,
                tool_schema_prompt=tool_schema_prompt,
                tool_history=tool_history,
                last_tool_result=last_tool_result,
                todos=state.get('todos') or [],
                current_todo_index=state.get('current_todo_index', 0) or 0,
            )
            
            import datetime as _dt
            _ts = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')

            # 记录完整的请求上下文（在调用前记录，确保异常时也有记录）
            try:
                with open('llm_decision_trace.log', 'a', encoding='utf-8') as _f:
                    _f.write(f"\n[{_ts}] === 🔄 LLM REQUEST START ===\n")
                    _f.write(f"[{_ts}] Agent: {agent_type}, Iteration: {iteration_count}/{max_iterations}\n")
                    _f.write(f"[{_ts}] Tool history: {len(tool_history)} items\n")
                    if tool_history:
                        for _idx, _item in enumerate(tool_history[-5:], 1):
                            _f.write(f"[{_ts}]   history[{_idx}]: tool={_item.get('tool_name', 'N/A')}, result_len={len(str(_item.get('result', '')))}\n")
                    _f.write(f"[{_ts}] Last tool result: {str(last_tool_result)[:500] if last_tool_result else 'None'}\n")
                    _f.write(f"\n[{_ts}] --- SYSTEM PROMPT (first 1000 chars) ---\n")
                    _f.write(system_prompt[:1000] if system_prompt else "(empty)")
                    _f.write(f"\n[{_ts}] --- USER MESSAGE (first 1000 chars) ---\n")
                    _f.write(context_prompt[:1000] if context_prompt else "(empty)")
                    _f.write(f"\n[{_ts}] === 🔄 LLM REQUEST END ===\n\n")
                    _f.flush()
            except Exception as _log_err:
                pass

            try:
                # 使用厂商 JSON Mode 强制返回纯 JSON，避免手工剥 ```json 包裹
                response = llm_service.chat_with_json_mode(
                    messages=[{"role": "user", "content": context_prompt}],
                    system_prompt=system_prompt,
                )

                response_text = response.strip()

                # 记录 LLM 原始响应
                with open('llm_decision_trace.log', 'a', encoding='utf-8') as _f:
                    _f.write(f"\n[{_ts}] === 🤖 LLM RAW RESPONSE ===\n")
                    _f.write(f"[{_ts}] Raw response ({len(response_text)} chars):\n{response_text}\n")
                    _f.flush()

                # 防御性处理：检查 LLM 是否返回空响应
                if not response_text:
                    raise ValueError("LLM 返回了空响应，可能是 API 超时或模型异常")

                decision_data = json.loads(response_text)
            except Exception as e:
                # 记录完整的异常信息
                import traceback as _tb
                import datetime as _dt
                _ts = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
                with open('llm_decision_trace.log', 'a', encoding='utf-8') as _f:
                    _f.write(f"\n[{_ts}] === ❌ LLM CALL EXCEPTION ===\n")
                    _f.write(f"[{_ts}] Exception Type: {type(e).__name__}\n")
                    _f.write(f"[{_ts}] Exception Message: {str(e)}\n")
                    _f.write(f"[{_ts}] Response object: {response if 'response' in locals() else 'NOT_AVAILABLE'}\n")
                    _f.write(f"[{_ts}] Response text: {response_text if 'response_text' in locals() else 'NOT_AVAILABLE'}\n")
                    _f.write(f"[{_ts}] Full Traceback:\n{_tb.format_exc()}\n")
                    _f.write(f"[{_ts}] === ❌ LLM CALL EXCEPTION END ===\n")
                    _f.flush()
                # 决策失败时尝试重试，而不是立即终止
                decision_error_count = (state.get("decision_error_count", 0) or 0) + 1
                max_decision_retries = 3

                if decision_error_count < max_decision_retries:
                    # 重试决策，不设置 final_reply 让 graph 继续
                    import datetime as _dt
                    _ts = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
                    with open('llm_decision_trace.log', 'a', encoding='utf-8') as _f:
                        _f.write(f"[{_ts}] ⚠️ 决策尝试 #{decision_error_count} 失败，将重试：{e}\n")
                        _f.flush()

                    return {
                        "next_action": None,
                        "final_reply": None,
                        "has_tool_use": False,
                        "pending_tools": [],
                        "decision_error_count": decision_error_count,
                    }

                # 重试次数用完，返回错误
                reply = f"当前无法自动决策下一步：{e}"
                return {
                    "next_action": {"kind": "reply", "reply": reply},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                    "decision_error_count": decision_error_count,
                }
            
            kind = decision_data.get("kind")
            if kind in ("step_done", "blocked"):
                # ===== 【关键修复】检查 Agent 是否遗漏了 chat 工具调用 =====
                if kind == "step_done":
                    has_document_call = any(t.get("tool_name") == "document" for t in tool_history)
                    has_chat_call = any(t.get("tool_name") == "chat" for t in tool_history)

                    if has_document_call and not has_chat_call:
                        # Agent 完成了数据分析但忘记调用 chat 工具输出结果
                        # 强制注入 chat 工具调用
                        console.warning("[_create_decide_node] Agent 遗漏了 chat 工具调用，强制注入最终回复输出")
                        return {
                            "pending_tools": [{"tool_name": "chat", "args": {
                                "description": "已完成分析并提取病害信息。请输出最终结果。"
                            }}],
                            "has_tool_use": True,
                            "todo_status": None,  # 清除状态，等待 chat 执行
                        }

                return {
                    "todo_status": kind,
                    "has_tool_use": False,
                    "pending_tools": [],
                }
            
            tool_name = decision_data.get("tool_name")
            tool_args = decision_data.get("tool_args") or {}
            reason = decision_data.get("reason", "")  # 工具调用原因
            
            if not tool_name or not is_tool_allowed(tool_name, agent_type, settings_service):
                retry_count = (state.get("invalid_tool_retry_count", 0) or 0) + 1
                if retry_count <= 3:
                    return {
                        "pending_tools": [],
                        "has_tool_use": False,
                        "final_reply": None,
                        "next_action": None,
                        "invalid_tool_retry_count": retry_count,
                    }
                reply = f"工具决策无效，无法继续执行：{tool_name}"
                return {
                    "next_action": {"kind": "reply", "reply": reply},
                    "final_reply": reply,
                    "has_tool_use": False,
                    "pending_tools": [],
                    "invalid_tool_retry_count": retry_count,
                }
            
            pending = [{"tool_name": tool_name, "args": dict(tool_args), "reason": reason}]
            return {
                "next_action": {
                    "kind": "tool",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "reason": reason,
                },
                "pending_tools": pending,
                "has_tool_use": True,
                "final_reply": None,
                "invalid_tool_retry_count": 0,
            }
        
        return decide_tool_action_node
    
    def _create_execute_node(self, llm_service=None, settings_service=None, message_context=None, post_execute_hook=None):
        from .subgraphs.tool_executor import run_tool_execution
        from singleton import get_workspace_service
        
        workspace_service = get_workspace_service()
        
        def execute_node(state: AgentState) -> dict:
            pending_tools = state.get("pending_tools", [])

            # 【关键修复】防止 pending_tools 为空时访问 [0] 导致 IndexError
            if not pending_tools:
                console.info("[_create_execute_node] pending_tools 为空，直接返回")
                return {
                    "pending_tools": [],
                    "has_tool_use": False,
                    "final_reply": state.get("last_tool_result") or "任务执行完成",
                }

            tool_name = pending_tools[0].get("tool_name")
            tool_args = pending_tools[0].get("args", {})
            reason = (
                pending_tools[0].get("reason")
                or (state.get("next_action") or {}).get("reason")
                or ""
            )
            
            enhanced_tool_args = self.memory_manager.inject_memory(
                tool_args=tool_args,
                state=state,
                memory_mode=self.definition.meta.memory_mode
            )
            
            parent_chain_messages = state.get("parent_chain_messages", [])
            current_conversation_messages = state.get("current_conversation_messages", [])
            
            enhanced_message_context = dict(message_context) if message_context else {}
            enhanced_message_context["workspace_id"] = state.get("workspace_id")
            enhanced_message_context["parent_chain_messages"] = parent_chain_messages
            enhanced_message_context["current_conversation_messages"] = current_conversation_messages
            
            tool_result = run_tool_execution(
                tool_name=tool_name,
                tool_args=enhanced_tool_args,
                workspace_id=state["workspace_id"],
                previous_calls=state.get("tool_history", []),
                workspace_service=workspace_service,
                llm_service=llm_service,
                token_callback=None,
                task_description=reason,
                previous_results=[item.get("result") for item in state.get("tool_history", []) if item.get("result")],
                agent_type=state.get("agent_type") or "director_agent",
                settings_service=settings_service,
                message_context=enhanced_message_context,
            )
            
            result_str = str(tool_result.get("result", "")) if tool_result.get("result") is not None else ""
            new_tool_history = state.get("tool_history", []) + [{
                "tool_name": tool_name,
                "args": tool_args,
                "reason": reason,
                "result": tool_result.get("result"),
                "timestamp": datetime.datetime.now().isoformat(),
            }]
            
            new_current_conv_msgs = list(current_conversation_messages)
            tool_error = tool_result.get("error")
            content = f"[工具执行: {tool_name}]\n结果: {result_str[:1000]}"
            if tool_error:
                content += f"\n错误: {tool_error}"
            new_current_conv_msgs.append({
                "role": "assistant",
                "content": content
            })
            
            tool_success = tool_result.get("error") is None
            execution_mode = state.get("execution_mode")
            
            _force_error_summary = False
            _error_summary_type = None
            if tool_error:
                if "DoomLoop" in str(tool_error) or "loop" in str(tool_error).lower():
                    _force_error_summary = True
                    _error_summary_type = f"doom_loop({tool_name})"
            
            direct_update = {
                "pending_tools": [],
                "tool_history": new_tool_history,
                "current_conversation_messages": new_current_conv_msgs,
                "has_tool_use": False,
                "last_tool_result": result_str,
                "last_tool_name": tool_name,
                "last_tool_success": tool_success,
                "last_tool_error": tool_error,
                "iteration_count": (state.get("iteration_count", 0) or 0) + 1,
                "current_todo_iteration_count": (state.get("current_todo_iteration_count", 0) or 0) + 1,
                "todo_status": "in_progress",
                "next_action": None,
            }
            
            if _force_error_summary:
                direct_update["force_error_summary"] = True
                direct_update["error_summary_type"] = _error_summary_type
            
            if execution_mode and tool_name != "chat":
                if post_execute_hook:
                    hook_result = post_execute_hook(direct_update, tool_result, state)
                    if hook_result:
                        direct_update.update(hook_result)
                
                return direct_update
            
            has_more_tools = len(pending_tools) > 1
            is_chat_tool = tool_name == "chat"
            
            if is_chat_tool:
                direct_update.update({
                    "pending_tools": pending_tools[1:],
                    "final_reply": result_str,
                })
                return direct_update
            
            direct_update.update({
                "pending_tools": pending_tools[1:],
                "has_tool_use": has_more_tools,
            })
            
            return direct_update
        
        return execute_node
    
    def _create_todo_review_node(self, llm_service=None, message_context=None):
        def step_review_node(state: AgentState) -> dict:
            todos = state.get("todos") or []

            # ===== 【关键修复】检测 Agent 忘记调用 chat 工具 =====
            # 条件：有工具调用历史 + 没有 pending_tools + 没有调用过 chat
            # 说明：Agent 已完成工作但忘记调用 chat 输出结果，强制注入 chat 工具
            tool_history = state.get("tool_history", []) or []
            has_chat_call = any(t.get("tool_name") == "chat" for t in tool_history)
            has_pending = bool(state.get("pending_tools"))

            if tool_history and not has_chat_call and not has_pending:
                from core.logging import console
                console.warning("[todo_review] 检测到 Agent 遗漏了 chat 工具调用，注入 chat 工具")
                # 【关键修复】直接返回 pending_tools，让 graph 回到 execute 节点
                return {
                    "pending_tools": [{"tool_name": "chat", "args": {
                        "description": "任务分析已完成，请输出最终结果。"
                    }}],
                    "has_tool_use": True,
                    "todo_status": None,
                }

            if not todos:
                return {
                    "todo_status": "continue",
                    "has_tool_use": False,
                    "pending_tools": [],
                    "todos": todos,
                }

            if state.get("last_tool_success") is False:
                return {
                    "todo_status": "blocked",
                    "has_tool_use": False,
                    "pending_tools": [],
                    "todos": todos,
                }

            return {
                "todo_status": state.get("todo_status") or "continue",
                "has_tool_use": False,
                "pending_tools": [],
                "todos": todos,
            }

        return step_review_node
    
    def _route_after_decide(self, state: AgentState) -> str:
        if state.get("final_reply"):
            return "done"

        # ===== 【关键修复】前置迭代次数检查，防止 LangGraph 递归限制 =====
        iteration_count = state.get("iteration_count", 0) or 0
        max_iterations = state.get("max_iterations", 10) or 10
        if iteration_count >= max_iterations:
            return "error_summary"

        next_action = state.get("next_action") or {}
        if next_action.get("kind") in ("reply", "enter_plan"):
            return "done"

        if state.get("pending_tools"):
            return "execute"

        # ===== 决策错误保护 =====
        # 当决策连续失败时，防止无限循环重试
        decision_error_count = state.get("decision_error_count", 0) or 0
        if decision_error_count >= 3:
            return "error_summary"

        # ===== 【关键修复】检查 Agent 是否遗漏了 chat 工具调用 =====
        # 条件：有工具调用历史 + 没有调用过 chat
        # 说明：Agent 已完成工作但忘记调用 chat 输出结果，强制注入 chat 工具
        # 注意：不强制要求 document 工具，因为 explore_agent 可能不需要调用 document
        tool_history = state.get("tool_history", []) or []
        has_chat_call = any(t.get("tool_name") == "chat" for t in tool_history)

        if tool_history and not has_chat_call:
            # Agent 完成了工作但忘记调用 chat 工具输出结果
            # 【关键修复】返回字符串节点名，而不是字典
            console.warning("[_route_after_decide] Agent 遗漏了 chat 工具调用，返回 execute 节点")
            return "execute"

        # 特殊情况：当 prediction agent 返回 step_done 但没有 pending_tools 时
        # 检查工具历史，如果已有分析数据但还没生成报告，强制调用 document
        todo_status = state.get("todo_status")
        if todo_status == "step_done":
            tool_history = state.get("tool_history", [])
            has_analysis_data = any(
                t.get("tool_name") in ("bridge_report_parser", "calculate_bci", "predict_trend")
                for t in tool_history
            )
            has_document_call = any(t.get("tool_name") == "document" for t in tool_history)

            if has_analysis_data and not has_document_call:
                # 强制注入 document 工具调用
                return "execute"

        return "done"
    
    def _route_after_execute(self, state: AgentState) -> str:
        if state.get("final_reply"):
            return "done"

        if state.get("force_error_summary"):
            return "error_summary"

        # ===== 【关键修复】前置迭代次数检查，防止 LangGraph 递归限制 =====
        iteration_count = state.get("iteration_count", 0) or 0
        max_iterations = state.get("max_iterations", 10) or 10
        if iteration_count >= max_iterations:
            return "error_summary"

        next_action = state.get("next_action") or {}
        if next_action.get("kind") == "enter_plan":
            return "done"

        if state.get("pending_tools"):
            return "execute"

        if state.get("force_error_summary"):
            return "error_summary"

        # ===== 新增：检测工具失败循环 =====
        # 当工具持续失败且没有进展时，强制结束循环
        iteration_count = state.get("iteration_count", 0) or 0
        last_tool_success = state.get("last_tool_success")
        last_tool_name = state.get("last_tool_name")
        last_tool_result = state.get("last_tool_result") or ""
        tool_history = state.get("tool_history", []) or []

        # 基础保护：如果迭代次数过多（超过15次）且没有pending_tools，强制终止
        if iteration_count > 15 and not state.get("pending_tools"):
            return "error_summary"

        # ===== 新增：检测 document 工具读取空内容循环 =====
        if last_tool_name == "document" and last_tool_success is not False:
            # document 工具没有报错，但检查是否返回空内容
            result_str = str(last_tool_result) if last_tool_result else ""
            is_empty_result = len(result_str) < 100  # 结果很短

            if is_empty_result:
                # 检查最近几次 document 读取是否都是空的
                recent_empty_reads = 0
                for item in reversed(tool_history[-6:]):
                    if item.get("tool_name") == "document":
                        result = str(item.get("result", "") or "")
                        if len(result) < 100:
                            recent_empty_reads += 1

                # 如果连续 3 次 document 读取都返回空内容，强制终止
                if recent_empty_reads >= 3:
                    return "error_summary"
        # ===== 结束新增 =====

        if last_tool_success is False and last_tool_name:
            # 检查最近是否有连续失败
            recent_failures = 0
            repeated_same_tool = 0
            last_failed_args = None

            for item in reversed(tool_history[-10:]):
                if item.get("tool_name") == last_tool_name:
                    if item.get("result") is None or item.get("error"):
                        recent_failures += 1
                        current_args = item.get("args", {})
                        if last_failed_args == current_args:
                            repeated_same_tool += 1
                        last_failed_args = current_args

            # 如果同一工具连续失败超过3次，且参数相同，强制报告问题
            if repeated_same_tool >= 3:
                return "error_summary"

            # 如果最近5次工具有4次以上失败，强制报告问题
            if recent_failures >= 4 and len(tool_history) >= 5:
                return "error_summary"
        # ===== 结束新增 =====

        return "decide"

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
