"""V4 编排图构建与运行入口。

主图（定稿 §3.2）：
    sidekick-analyze -> leader-reasoning <-> leader-acting
                     -> sidekick-closuring -> finalize -> END

异常终止（固定模板）由 reasoning 直接路由 finalize（跳过 closuring）。
子代理（explore/review/prediction/plan）使用同一 reasoning/acting 骨架，
但关闭 todo_review 与 closuring（其 tool_res 即 text，leader 是子代理的用户）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from ...state import AgentState
from core.logging import console

from .analyze import create_analyze_node, route_after_analyze
from .reasoning import create_reasoning_node, route_after_reasoning
from .acting import create_acting_node, route_after_acting
from .closuring import create_closuring_node, route_after_closuring
from .finalize import create_finalize_node


# conversation_id -> (compiled_graph, config) 用于 resume 恢复（进程内，亲和性保证同实例）
_RESUME_REGISTRY: dict[str, tuple] = {}


def _v4_enabled(settings_service) -> bool:
    if settings_service is None:
        return False
    try:
        return str(settings_service.get("agent:orchestration_version") or "v3").lower() == "v4"
    except Exception:
        return False


def build_v4_graph(
    *,
    llm_service=None,
    token_callback=None,
    settings_service=None,
    message_context: Optional[dict] = None,
    post_execute_hook: Optional[Callable] = None,
    enable_todo: bool = True,
    enable_interrupt: bool = False,
) -> Any:
    """构建 V4 主图（或子代理循环图）。"""
    graph = StateGraph(AgentState)

    graph.add_node("sidekick_analyze", create_analyze_node(
        llm_service,
        message_context,
        settings_service,
    ))
    graph.add_node("leader_reasoning", create_reasoning_node(
        llm_service=llm_service,
        settings_service=settings_service,
        message_context=message_context,
    ))
    graph.add_node("leader_acting", create_acting_node(
        llm_service=llm_service,
        token_callback=token_callback,
        settings_service=settings_service,
        message_context=message_context,
        post_execute_hook=post_execute_hook,
    ))
    graph.add_node("sidekick_closuring", create_closuring_node(
        llm_service=llm_service,
        settings_service=settings_service,
        message_context=message_context,
    ))
    graph.add_node("finalize", create_finalize_node(message_context))

    graph.set_entry_point("sidekick_analyze")

    graph.add_edge("sidekick_analyze", "leader_reasoning")

    graph.add_conditional_edges(
        "leader_reasoning",
        route_after_reasoning,
        {
            "acting": "leader_acting",
            "closuring": "sidekick_closuring",
            "reasoning": "leader_reasoning",
            "finalize": "finalize",
            "done": END,
        },
    )

    graph.add_conditional_edges(
        "leader_acting",
        route_after_acting,
        {
            "reasoning": "leader_reasoning",
            "finalize": "finalize",
            "done": END,
        },
    )

    graph.add_conditional_edges(
        "sidekick_closuring",
        route_after_closuring,
        {
            "finalize": "finalize",
            "reasoning": "leader_reasoning",
            "done": END,
        },
    )

    graph.add_edge("finalize", END)

    if enable_interrupt:
        return graph.compile(checkpointer=InMemorySaver())
    return graph.compile()


def run_v4_graph(
    user_message: Any,
    workspace_id: str,
    llm_service=None,
    token_callback=None,
    memory_mode: str = "accumulate",
    window_size: int = 3,
    settings_service=None,
    message_context: dict = None,
    parent_chain_messages: list = None,
    current_conversation_messages: list = None,
    prior_agent_state: Optional[dict] = None,
    agent_type: str = "director_agent",
    post_execute_hook: Optional[Callable] = None,
    enable_todo: bool = True,
    conversation_id: Optional[str] = None,
) -> dict:
    """运行 V4 编排图，返回 final_state（与 run_graph_v2/v3 兼容）。"""
    from ..definitions import get_definition
    from ..director_agent import build_initial_state
    from ..agent_definition import calculate_recursion_limit

    definition = get_definition(agent_type)
    if conversation_id is None and message_context:
        conversation_id = message_context.get("conversation_id")

    initial_state = build_initial_state(
        user_message=user_message,
        workspace_id=workspace_id,
        definition=definition,
        parent_chain_messages=parent_chain_messages,
        current_conversation_messages=current_conversation_messages,
        agent_type=agent_type,
        is_root_graph=True,
        prior_agent_state=prior_agent_state,
    )
    initial_state["agent_type"] = agent_type
    initial_state["tool_records"] = initial_state.get("tool_records") or []
    initial_state["max_iterations"] = definition.meta.max_iterations

    graph = build_v4_graph(
        llm_service=llm_service,
        token_callback=token_callback,
        settings_service=settings_service,
        message_context=message_context,
        post_execute_hook=post_execute_hook,
        enable_todo=enable_todo,
        enable_interrupt=True,
    )

    thread_id = conversation_id or f"v4-{workspace_id}-{agent_type}"
    graph_config = {
        "recursion_limit": calculate_recursion_limit(definition.meta.max_iterations),
        "configurable": {"thread_id": thread_id},
    }

    try:
        final_state = graph.invoke(initial_state, config=graph_config)
    except GraphInterrupt as gi:
        # 部分 langgraph 版本 invoke 会抛 GraphInterrupt
        final_state = {"__interrupt__": getattr(gi, "interrupts", [])}

    if isinstance(final_state, dict) and final_state.get("__interrupt__"):
        interrupts = final_state["__interrupt__"]
        try:
            payload = interrupts[0].value
        except Exception:
            payload = None
        _RESUME_REGISTRY[thread_id] = (graph, graph_config)
        console.info(f"[run_v4_graph] 交互中断（awaiting_user_input）: {payload}")
        return {
            "status": "awaiting_user_input",
            "interrupt": payload,
            "thread_id": thread_id,
        }

    console.info(
        f"[run_v4_graph] {agent_type} 执行完成，"
        f"final_reply={bool(final_state.get('final_reply'))}, "
        f"rounds={final_state.get('iteration_count', 0)}"
    )
    return final_state


def resume_v4_graph(thread_id: str, answer: Any) -> dict:
    """恢复被 ask_user_question 中断的图（由 resume 端点调用）。"""
    entry = _RESUME_REGISTRY.get(thread_id)
    if not entry:
        raise KeyError(f"未找到可恢复的 v4 会话: {thread_id}")
    graph, graph_config = entry
    final_state = graph.invoke(Command(resume=answer), config=graph_config)
    _RESUME_REGISTRY.pop(thread_id, None)
    return final_state


def build_v4_child_loop(
    llm_service=None,
    token_callback=None,
    settings_service=None,
    message_context: Optional[dict] = None,
) -> Any:
    """子代理循环图：reasoning/acting，无 todo、无 closuring。"""
    return build_v4_graph(
        llm_service=llm_service,
        token_callback=token_callback,
        settings_service=settings_service,
        message_context=message_context,
        post_execute_hook=None,
        enable_todo=False,
        enable_interrupt=False,
    )
