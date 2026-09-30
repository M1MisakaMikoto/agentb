"""MCP 工具登记/配置化相关单测：登记清单、未登记忽略、缺失告警、按工具选项、权限配置。"""

import os
import sys

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

import singleton
from service.agent_service.tools import mcp_client
from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools
from service.settings_service.settings_service import DEFAULT_SETTINGS


class _FakeSettings:
    def __init__(self, data):
        self._data = data

    def get(self, key):
        node = self._data
        for part in key.split(":"):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"Setting key not found: '{key}'")
            node = node[part]
        return node


class _FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} 描述"
        self.inputSchema = {
            "type": "object",
            "properties": {"arg": {"type": "string", "description": "参数"}},
            "required": ["arg"],
        }


def _default_mcp_settings(tools=None):
    return {
        "agent_tools": {
            "mcp": {
                "upstream_tools": {
                    "url": "http://127.0.0.1:8181/mcp",
                    "timeout_seconds": 60,
                    "probe_timeout_seconds": 5,
                    "tools": tools
                    if tools is not None
                    else ["submit_facility_report", "submit_ai_judgment_issue"],
                    "tool_options": {
                        "submit_facility_report": {
                            "file_args": ["reportFile"],
                            "timeout_seconds": 123,
                        }
                    },
                }
            }
        }
    }


def _use_settings(monkeypatch, data):
    monkeypatch.setattr(singleton, "get_settings_service", lambda: _FakeSettings(data))


def test_default_config_tool_list_matches_registry_default():
    """默认配置里的工具清单必须与 registry 的默认集合一致（防止两处漂移）。"""
    from service.agent_service.tools.registry import MCP_TOOLS

    default_list = DEFAULT_SETTINGS["agent_tools"]["mcp"]["upstream_tools"]["tools"]
    assert sorted(default_list) == sorted(MCP_TOOLS)


def test_classify_splits_registered_unlisted_missing(monkeypatch):
    _use_settings(
        monkeypatch,
        _default_mcp_settings(tools=["submit_facility_report", "submit_missing_tool"]),
    )

    outcome = mcp_client.classify_mcp_tools(
        [_FakeTool("submit_facility_report"), _FakeTool("submit_unlisted_tool")]
    )

    assert outcome["registered"] == ["submit_facility_report"]
    assert outcome["unlisted"] == ["submit_unlisted_tool"]
    assert outcome["missing"] == ["submit_missing_tool"]
    assert set(outcome["entries"].keys()) == {"submit_facility_report"}
    assert "submit_unlisted_tool" not in outcome["entries"]


def test_empty_tool_list_raises(monkeypatch):
    _use_settings(monkeypatch, _default_mcp_settings(tools=[]))
    with pytest.raises(ValueError, match="tools"):
        mcp_client.configured_mcp_tools()


def test_tool_options_file_args_and_timeout(monkeypatch):
    _use_settings(monkeypatch, _default_mcp_settings())

    assert mcp_client.mcp_tool_file_args("submit_facility_report") == ["reportFile"]
    assert mcp_client.mcp_tool_timeout_seconds("submit_facility_report") == 123
    # 未配置的工具：无文件参数、超时回落全局值
    assert mcp_client.mcp_tool_file_args("submit_ai_judgment_issue") == []
    assert mcp_client.mcp_tool_timeout_seconds("submit_ai_judgment_issue") == 60


def test_tool_permissions_config_drives_allowed_tools(monkeypatch):
    data = {
        "tool_permissions": {
            "director_agent": {
                "allowed": ["read_file", "chat", "submit_new_mcp_tool", "ask_user_question"]
            }
        },
        "agent": {"ask_user_question_enabled": False},
    }
    settings = _FakeSettings(data)

    allowed = get_allowed_tools("director_agent", settings)

    assert "submit_new_mcp_tool" in allowed
    assert "chat" not in allowed          # 退役工具被统一过滤
    assert "ask_user_question" not in allowed  # 关闭开关后过滤
    assert allowed == ["read_file", "submit_new_mcp_tool"]


def test_tool_permissions_bad_shape_raises():
    settings = _FakeSettings({"tool_permissions": {"director_agent": {"allowed": "read_file"}}})

    with pytest.raises(ValueError, match="allowed"):
        get_allowed_tools("director_agent", settings)


def test_allowed_tools_falls_back_to_definition_without_config():
    """没有 tool_permissions 配置时，仍走 AgentDefinition（历史路径）。"""
    allowed = get_allowed_tools("director_agent", _FakeSettings({}))

    assert "read_file" in allowed
    assert "submit_facility_report" in allowed
