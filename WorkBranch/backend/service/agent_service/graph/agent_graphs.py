from typing import List, Dict, Optional, Any

from langgraph.graph import StateGraph, END

from singleton import get_workspace_service

from .director_agent import build_initial_state, create_orchestrator_graph_v3, _start_fresh_direct_run_from_plan
from .decision.complexity_analyzer import ExecutionMode
from .subgraphs import run_tool_execution
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


def _build_default_tools(agent_type: str, user_message: str) -> list[dict]:
    if agent_type == "explore_agent":
        return [
            {"tool": "thinking", "args": {"description": user_message}},
            {"tool": "chat", "args": {"description": user_message}},
        ]
    if agent_type == "review_agent":
        return [
            {"tool": "thinking", "args": {"description": user_message}},
            {"tool": "chat", "args": {"description": user_message}},
        ]
    return []


AGENT_GRAPH_CONFIG = {
    "director_agent": {
        "execution_mode": None,
    },
    "explore_agent": {
        "execution_mode": ExecutionMode.DIRECT,
    },
    "review_agent": {
        "execution_mode": ExecutionMode.DIRECT,
    },
}


def create_child_agent_graph(
    agent_type: str,
    llm_service=None,
    token_callback=None,
    settings_service=None,
    message_context: dict = None,
):
    graph = StateGraph(AgentState)

    def execute_child_node(state: AgentState) -> dict:
        pending_tools = state.get("pending_tools", []) or []
        if not pending_tools:
            return {
                "final_reply": state.get("final_reply"),
                "has_tool_use": False,
                "pending_tools": [],
            }

        tool_entry = pending_tools[0]
        tool_name = tool_entry.get("tool")
        tool_args = tool_entry.get("args", {})
        tool_result = run_tool_execution(
            tool_name=tool_name,
            tool_args=tool_args,
            workspace_id=state["workspace_id"],
            previous_calls=state.get("tool_history", []),
            workspace_service=get_workspace_service(),
            llm_service=llm_service,
            token_callback=token_callback,
            task_description=tool_args.get("description", ""),
            previous_results=[item.get("result") for item in state.get("tool_history", []) if item.get("result")],
            agent_type=agent_type,
            settings_service=settings_service,
            message_context=message_context,
        )

        result_str = str(tool_result.get("result", "")) if tool_result.get("result") is not None else ""
        new_history = state.get("tool_history", []) + [{
            "tool": tool_name,
            "args": tool_args,
            "result": tool_result.get("result")
        }]

        if tool_name == "thinking":
            remaining = pending_tools[1:]
            if not remaining:
                remaining = [{"tool": "chat", "args": {"description": state["messages"][-1] if state.get("messages") else ""}}]
            return {
                "tool_history": new_history,
                "pending_tools": remaining,
                "has_tool_use": bool(remaining),
            }

        if tool_name == "chat":
            return {
                "tool_history": new_history,
                "pending_tools": [],
                "has_tool_use": False,
                "final_reply": result_str,
            }

        remaining = pending_tools[1:]
        return {
            "tool_history": new_history,
            "pending_tools": remaining,
            "has_tool_use": bool(remaining),
            "final_reply": result_str or state.get("final_reply"),
        }

    def route_child(state: AgentState) -> str:
        if state.get("final_reply"):
            return "done"
        if state.get("pending_tools"):
            return "execute"
        return "done"

    graph.add_node("execute", execute_child_node)
    graph.set_conditional_entry_point(route_child, {
        "execute": "execute",
        "done": END,
    })
    graph.add_conditional_edges("execute", route_child, {
        "execute": "execute",
        "done": END,
    })
    return graph.compile()


def create_agent_graph(
    agent_type: str,
    llm_service=None,
    token_callback=None,
    memory_mode: str = "accumulate",
    window_size: int = 3,
    settings_service=None,
    message_context: dict = None,
):
    if agent_type in {"explore_agent", "review_agent"}:
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
    user_message: str,
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
    from service.settings_service.settings_service import SettingsService
    from service.agent_service.service.llm_service import get_llm_service

    config = AGENT_GRAPH_CONFIG.get(agent_type, AGENT_GRAPH_CONFIG["director_agent"])

    if settings_service is None:
        settings_service = SettingsService()

    if llm_service is None:
        llm_service = get_llm_service(settings_service)

    saved_state = persistence.load(workspace_id) if persist_state else None

    if saved_state:
        initial_state = saved_state
        initial_state["messages"] = initial_state.get("messages", []) + [user_message]
    else:
        initial_state = build_initial_state(
            user_message=user_message,
            workspace_id=workspace_id,
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            agent_type=agent_type,
        )

    initial_state["agent_type"] = agent_type

    if config.get("execution_mode") is not None:
        initial_state["execution_mode"] = config["execution_mode"]
        initial_state["has_tool_use"] = bool(initial_state.get("pending_tools"))
        if not initial_state.get("pending_tools"):
            initial_state["pending_tools"] = _build_default_tools(agent_type, user_message)
            initial_state["has_tool_use"] = bool(initial_state.get("pending_tools"))

    graph = create_agent_graph(
        agent_type=agent_type,
        llm_service=llm_service,
        token_callback=token_callback,
        memory_mode=memory_mode,
        window_size=window_size,
        settings_service=settings_service,
        message_context=message_context,
    )
    final_state = graph.invoke(initial_state)

    if agent_type == "director_agent" and final_state.get("execution_mode") == ExecutionMode.PLAN and final_state.get("plan_file"):
        final_state = _start_fresh_direct_run_from_plan(
            user_message=user_message,
            workspace_id=workspace_id,
            llm_service=llm_service,
            token_callback=token_callback,
            memory_mode=memory_mode,
            window_size=window_size,
            settings_service=settings_service,
            message_context=message_context,
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
        )

    if persist_state:
        persistence.save(workspace_id, final_state)

    return build_agent_outcome(agent_type, final_state)
