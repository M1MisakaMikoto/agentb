from typing import List, Dict, Optional, Any

from langgraph.graph import StateGraph, END

from singleton import get_workspace_service
from core.logging import console, open_trace_log

from .director_agent import build_initial_state, create_orchestrator_graph_v3, get_last_user_message_text
from .decision.complexity_analyzer import ExecutionMode
from .subgraphs import run_tool_execution
from .react_agent_base import ReActAgentBase
from .definitions import get_definition
from .agent_definition import calculate_recursion_limit
from ..persistence import PersistenceService
from ..state import AgentState


persistence = PersistenceService()


def build_agent_outcome(agent_type: str, final_state: dict) -> dict:
    final_reply = final_state.get("final_reply")
    error = final_state.get("error")
    if error:
        status = "failed"
        payload = None
        exit_info = {
            "code": "graph_error",
            "message": str(error),
            "details": {"agent_type": agent_type},
        }
    elif final_reply:
        status = "completed"
        payload = final_reply
        exit_info = {
            "code": "final_reply",
            "message": None,
            "details": {"agent_type": agent_type},
        }
    else:
        status = "completed"
        payload = None
        exit_info = {
            "code": "graph_finished_without_reply",
            "message": None,
            "details": {"agent_type": agent_type},
        }

    return {
        "kind": "graph",
        "agent_type": agent_type,
        "status": status,
        "payload": payload,
        "produced_user_reply": bool(final_reply),
        "exit_info": exit_info,
        "final_state": final_state,
    }


    # 注意：_build_default_tools 和 AGENT_GRAPH_CONFIG 已删除
    # 统一从 AgentDefinition 获取配置
    # 符合 REFACTORING_PLAN.md 方案要求：消除双轨制配置


def create_child_agent_graph(
    agent_type: str,
    llm_service=None,
    token_callback=None,
    settings_service=None,
    message_context: dict = None,
):
    # V4 编排开关：子代理走统一 reasoning/acting 骨架
    try:
        if settings_service is not None and str(
            settings_service.get("agent:orchestration_version") or "v3"
        ).lower() == "v4":
            from .v4.graph import build_v4_child_loop
            return build_v4_child_loop(
                llm_service=llm_service,
                settings_service=settings_service,
                message_context=message_context,
            )
    except Exception as e:
        console.warning(f"[create_child_agent_graph] V4 子图切换失败，回退 V3: {e}")

    try:
        definition = get_definition(agent_type)
        child_base = ReActAgentBase(definition=definition)
        console.info(f"[create_child_agent_graph] ✅ 使用 ReActAgentBase 初始化 {agent_type}")
    except (ValueError, KeyError) as e:
        console.warning(f"[create_child_agent_graph] ⚠️ 未找到 {agent_type} 定义: {e}，使用默认配置")
        child_base = ReActAgentBase(definition=get_definition("prediction_agent"))
    
    simple_config = {
        "enable_todo": False,
        "post_execute_hook": None,
        "llm_service": llm_service,
        "settings_service": settings_service,
        "message_context": message_context,
    }
    
    loop_graph = child_base.build_react_loop_graph(simple_config)
    
    return loop_graph


def create_agent_graph(
    agent_type: str,
    llm_service=None,
    token_callback=None,
    memory_mode: str = "accumulate",
    window_size: int = 3,
    settings_service=None,
    message_context: dict = None,
):
    if agent_type in {"explore_agent", "review_agent", "prediction_agent"}:
        return create_child_agent_graph(
            agent_type=agent_type,
            llm_service=llm_service,
            token_callback=token_callback,
            settings_service=settings_service,
            message_context=message_context,
        )

    return create_orchestrator_graph_v3(
        llm_service=llm_service,
        token_callback=token_callback,
        memory_mode=memory_mode,
        window_size=window_size,
        settings_service=settings_service,
        message_context=message_context,
    )


def run_agent_graph(
    agent_type: str,
    user_message: Any,
    workspace_id: str,
    llm_service=None,
    token_callback=None,
    memory_mode: str = "accumulate",
    window_size: int = 3,
    settings_service=None,
    message_context: dict = None,
    parent_chain_messages: Optional[List[dict]] = None,
    current_conversation_messages: Optional[List[dict]] = None,
    persist_state: bool = False,
) -> dict:
    import datetime
    from service.settings_service.settings_service import SettingsService
    from service.agent_service.service.llm_service import get_llm_service

    definition = None
    try:
        definition = get_definition(agent_type)
        config = {"execution_mode": definition.get_execution_mode()}
    except (ValueError, KeyError):
        config = {"execution_mode": None}

    if settings_service is None:
        settings_service = SettingsService()

    if llm_service is None:
        llm_service = get_llm_service(settings_service)

    saved_state = persistence.load(workspace_id) if persist_state else None

    if saved_state:
        initial_state = saved_state
        initial_state["messages"] = initial_state.get("messages", []) + [user_message]
        initial_state["current_user_message_text"] = get_last_user_message_text(initial_state)
    else:
        initial_state = build_initial_state(
            user_message=user_message,
            workspace_id=workspace_id,
            definition=definition,
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            agent_type=agent_type,
        )

    initial_state["agent_type"] = agent_type

    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    
    with open_trace_log() as f:
        f.write(f"\n[{timestamp}] === RUN_AGENT_GRAPH ({agent_type}) ===\n")
        f.write(f"Config: {config}\n")
        f.write(f"Initial State - has_tool_use (before): {initial_state.get('has_tool_use')}\n")
        f.write(f"Initial State - pending_tools (before): {initial_state.get('pending_tools')}\n")
        f.flush()

    if config.get("execution_mode") is not None:
        initial_state["execution_mode"] = config["execution_mode"]
        initial_state["has_tool_use"] = bool(initial_state.get("pending_tools"))
        
        with open_trace_log() as f:
            f.write(f"[{timestamp}] Setting execution_mode: {config['execution_mode']}\n")
            f.write(f"[{timestamp}] has_tool_use after mode set: {initial_state.get('has_tool_use')}\n")
            f.flush()
            
        if not initial_state.get("pending_tools"):
            try:
                definition = get_definition(agent_type)
                default_tools = definition.meta.get_default_tools(get_last_user_message_text(initial_state))
            except (ValueError, KeyError):
                default_tools = [
                    {"tool": "thinking", "args": {"description": get_last_user_message_text(initial_state)}},
                    {"tool": "chat", "args": {"description": get_last_user_message_text(initial_state)}},
                ]
            
            with open_trace_log() as f:
                f.write(f"[{timestamp}] Building default tools: {default_tools}\n")
                f.flush()
                
            initial_state["pending_tools"] = default_tools
            initial_state["has_tool_use"] = bool(default_tools)
            
            with open_trace_log() as f:
                f.write(f"[{timestamp}] Final pending_tools: {initial_state.get('pending_tools')}\n")
                f.write(f"[{timestamp}] Final has_tool_use: {initial_state.get('has_tool_use')}\n")
                f.flush()

    graph = create_agent_graph(
        agent_type=agent_type,
        llm_service=llm_service,
        token_callback=token_callback,
        memory_mode=memory_mode,
        window_size=window_size,
        settings_service=settings_service,
        message_context=message_context,
    )
    import traceback
    try:
        # 【关键修复】通过 config 传递 recursion_limit，防止 LangGraph 递归限制
        # 决策失败可能导致多次重试，需要足够的递归深度
        max_iterations = initial_state.get('max_iterations', 10) or 10
        graph_config = {'recursion_limit': calculate_recursion_limit(max_iterations)}

        with open_trace_log() as f:
            f.write(f"[{timestamp}] Graph invoke with recursion_limit: {graph_config['recursion_limit']}\n")
            f.flush()

        final_state = graph.invoke(initial_state, config=graph_config)
    except Exception as e:
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        with open_trace_log() as f:
            f.write(f"\n[{timestamp}] === GRAPH INVOKE EXCEPTION ===\n")
            f.write(f"Exception Type: {type(e).__name__}\n")
            f.write(f"Exception Message: {str(e)}\n")
            f.write(f"Full Traceback:\n{traceback.format_exc()}\n")
            f.write(f"Initial State keys: {list(initial_state.keys())}\n")
            if initial_state.get('pending_tools'):
                f.write(f"pending_tools[0] type: {type(initial_state['pending_tools'][0])}\n")
                f.write(f"pending_tools[0] value: {initial_state['pending_tools'][0]}\n")
            if initial_state.get('next_action'):
                f.write(f"next_action type: {type(initial_state['next_action'])}\n")
                f.write(f"next_action value: {initial_state['next_action']}\n")
            f.flush()
        raise

    if persist_state:
        persistence.save(workspace_id, final_state)

    return build_agent_outcome(agent_type, final_state)
