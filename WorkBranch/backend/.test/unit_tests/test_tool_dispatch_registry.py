"""工具分发注册表一致性测试：所有可被模型调用的工具都必须有归属出口。"""

import os
import sys


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.subgraphs.tool_executor import (  # noqa: E402
    EXTERNAL_EXECUTION_TOOLS,
    build_tool_executors,
    registered_executor_tool_names,
)
from service.agent_service.graph.subgraphs.tool_registry import (  # noqa: E402
    SPECIAL_TOOLS,
    get_allowed_tools,
)


AGENT_TYPES = (
    "director_agent",
    "prediction_agent",
    "explore_agent",
    "review_agent",
    "plan_agent",
)


def test_every_allowed_tool_has_an_execution_path():
    registered = registered_executor_tool_names()
    missing = []
    for agent_type in AGENT_TYPES:
        for tool_name in get_allowed_tools(agent_type):
            if (
                tool_name in registered
                or tool_name in SPECIAL_TOOLS
                or tool_name in EXTERNAL_EXECUTION_TOOLS
            ):
                continue
            missing.append(f"{agent_type}:{tool_name}")
    assert not missing, f"以下工具没有执行出口（注册表/特殊工具/外部处理都没有）：{missing}"


def test_registry_builds_callable_executors_for_builtin_and_mcp_tools():
    executors = build_tool_executors(
        {
            "workspace_id": "ws-1",
            "workspace_service": None,
            "llm_service": None,
            "token_callback": None,
            "message_context": {"conversation_id": "conv-1"},
        }
    )

    assert executors, "注册表为空"
    assert all(callable(fn) for fn in executors.values())

    for required in (
        "read_file",
        "write_file",
        "document",
        "sql_query",
        "rag_search",
        "skill",
        "analyze_image",
        "call_prediction_agent",
        "calculate_bci",
        "list_workspace_files",
        "submit_facility_report",
        "submit_dailypatrol_record",
        "submit_ai_judgment_issue",
    ):
        assert required in executors, f"{required} 未注册执行器"


def test_unknown_tool_returns_explicit_error(monkeypatch):
    """未注册工具应返回明确错误，而不是伪造成功。"""
    import service.agent_service.graph.subgraphs.tool_executor as tool_executor

    monkeypatch.setattr(tool_executor, "open_trace_log", lambda: _NullContext())

    result = tool_executor.execute_tool(
        {
            "tool_name": "not_a_registered_tool",
            "tool_args": {},
            "workspace_id": "ws-1",
        }
    )

    assert result["result"] is None
    assert "未注册执行器" in result["error"]


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def write(self, _text):
        return 0

    def flush(self):
        return None
