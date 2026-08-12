from contextlib import nullcontext
from copy import deepcopy
from io import StringIO
import json
import os
import sys

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from controller import settings_api
from service.agent_service.graph import director_agent
from service.agent_service.graph import agent_graphs
from service.agent_service.graph import react_agent_base as react_agent_base_module
from service.agent_service.graph.agent_definition import calculate_recursion_limit
from service.agent_service.graph.director_agent import build_initial_state
from service.agent_service.graph.react_agent_base import ReActAgentBase
from service.agent_service.graph.definitions import get_definition
from service.agent_service.graph.subgraphs import tool_executor
from service.agent_service.graph.subgraphs.tool_executor import _get_subagent_timeout
from service.agent_service.prompts.graph_prompts import _format_tool_history
from service.settings_service.settings_service import (
    DEFAULT_SETTINGS,
    DEFAULT_SETTINGS_METADATA,
    _validate_runtime_limits,
)


class FakeSettings:
    def __init__(self):
        self.data = deepcopy(DEFAULT_SETTINGS)
        self.data["llm"]["api_key"] = "secret"
        self.data["intent_analysis"]["rule_keywords"] = ["keep-visible"]
        # 旧版 ReActAgentBase 已弃用但代码保留，本文件直接对其内部行为做单元验证。

    def reload(self):
        return None

    def get(self, key):
        node = self.data
        for part in key.split(":"):
            node = node[part]
        return node

    def get_all(self):
        return deepcopy(self.data)

    def get_metadata(self):
        return deepcopy(DEFAULT_SETTINGS_METADATA)

    def update_settings(self, updates):
        candidate = deepcopy(self.data)
        candidate.update(updates)
        _validate_runtime_limits(candidate)
        self.data = candidate


def test_all_agent_definitions_and_generic_state_use_32_iterations():
    for agent_type in ("director_agent", "prediction_agent", "explore_agent", "review_agent"):
        definition = get_definition(agent_type)
        assert definition.meta.max_iterations == 32
        expected_timeout = 1200 if agent_type == "director_agent" else 1800
        assert definition.meta.timeout_seconds == expected_timeout
        state = build_initial_state("task", "workspace", definition=definition, agent_type=agent_type)
        assert state["max_iterations"] == 32


def test_active_route_allows_iteration_16_and_stops_after_iteration_32():
    base = ReActAgentBase(get_definition("explore_agent"))
    state = {
        "iteration_count": 16,
        "max_iterations": 32,
        "pending_tools": [],
        "final_reply": None,
        "force_error_summary": False,
        "next_action": {},
        "tool_history": [],
        "last_tool_success": True,
        "last_tool_name": "read_file",
        "last_tool_result": "x" * 101,
    }
    assert base._route_after_execute(state) == "decide"
    state["iteration_count"] = 32
    assert base._route_after_execute(state) == "error_summary"


def test_generic_agent_entry_injects_definition_limit_into_state(monkeypatch):
    class FakeGraph:
        def invoke(self, state, config):
            assert config["recursion_limit"] == 170
            return state

    monkeypatch.setattr(agent_graphs, "create_agent_graph", lambda **_kwargs: FakeGraph())
    monkeypatch.setattr(agent_graphs, "open_trace_log", lambda: nullcontext(StringIO()))
    outcome = agent_graphs.run_agent_graph(
        "explore_agent",
        "task",
        "workspace",
        llm_service=object(),
        settings_service=FakeSettings(),
    )

    assert outcome["final_state"]["max_iterations"] == 32


def test_legacy_loop_respects_32_round_budget_without_chat(monkeypatch):
    # v2/v3 已弃用且 chat 工具已退役：旧 ReActAgentBase 循环不再用 chat 终止，
    # 由 32 轮预算强制结束。
    class ThirtyTwoRoundLLM:
        def __init__(self):
            self.calls = 0

        def chat_with_json_mode(self, **_kwargs):
            self.calls += 1
            return json.dumps({
                "kind": "tool",
                "tool_name": "thinking",
                "tool_args": {"description": f"round-{self.calls}"},
            })

    monkeypatch.setattr(
        director_agent,
        "_check_loop_or_stuck",
        lambda *_args, **_kwargs: {"action": "continue"},
    )
    monkeypatch.setattr(
        react_agent_base_module,
        "open_trace_log",
        lambda: nullcontext(StringIO()),
    )
    monkeypatch.setattr(
        tool_executor,
        "run_tool_execution",
        lambda tool_name, **_kwargs: {
            "result": "continue",
            "error": None,
        },
    )

    definition = get_definition("explore_agent")
    llm = ThirtyTwoRoundLLM()
    graph = ReActAgentBase(definition).build_react_loop_graph({
        "enable_todo": False,
        "llm_service": llm,
        "settings_service": FakeSettings(),
        "message_context": {},
    })
    state = build_initial_state(
        "long task",
        "workspace",
        definition=definition,
        agent_type="explore_agent",
    )
    final_state = graph.invoke(
        state,
        config={"recursion_limit": calculate_recursion_limit(32)},
    )

    assert llm.calls == 32
    assert final_state["iteration_count"] == 32


def test_limits_timeouts_and_recursion_budget_are_coherent():
    _validate_runtime_limits(deepcopy(DEFAULT_SETTINGS))
    invalid = deepcopy(DEFAULT_SETTINGS)
    invalid["agent"]["iterations"]["review"]["max"] = 257
    with pytest.raises(ValueError, match="1 到 256"):
        _validate_runtime_limits(invalid)

    settings = FakeSettings()
    assert _get_subagent_timeout(settings) == 1800
    assert calculate_recursion_limit(32) == 170


def test_tool_history_prompt_is_bounded_without_mutating_full_state():
    history = [
        {"tool_name": "read_file", "args": {"path": str(index)}, "result": "x" * 10000}
        for index in range(32)
    ]
    prompt = _format_tool_history(history)
    assert "旧记录已归档" in prompt
    assert "中间省略" in prompt
    assert len(prompt) < 40000
    assert all(len(item["result"]) == 10000 for item in history)


def test_settings_api_is_registered_redacts_secrets_and_preserves_them(monkeypatch):
    settings = FakeSettings()
    monkeypatch.setattr(settings_api, "get_settings_service", lambda: settings)

    assert {route.path for route in settings_api.router.routes} == {
        "/api/settings",
        "/api/settings/metadata",
    }
    response_data = settings_api.read_settings().data
    assert response_data["llm"]["api_key"] == ""
    assert response_data["intent_analysis"]["rule_keywords"] == ["keep-visible"]

    updated_llm = deepcopy(settings.data["llm"])
    updated_llm["api_key"] = ""
    updated_llm["temperature"] = 0.2
    settings_api.patch_settings({"llm": updated_llm})
    assert settings.data["llm"]["api_key"] == "secret"
    assert settings.data["llm"]["temperature"] == 0.2
