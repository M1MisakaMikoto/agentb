import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKBRANCH_DIR = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
for path in (BACKEND_DIR, WORKBRANCH_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.upstream_tools import config as upstream_cfg
from mcp_servers.upstream_tools import server as upstream_server
from service.agent_service.tools import mcp_client, mcp_health
from service.agent_service.tools.registry import ALL_TOOLS, MCP_TOOLS
from service.agent_service.graph.subgraphs import tool_executor


TMP_ROOT = Path(WORKBRANCH_DIR).parent / ".temp" / "mcp_upstream_tests"


@pytest.fixture
def work_tmp_dir():
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=str(TMP_ROOT)))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _FakeTool:
    def __init__(self, name, schema):
        self.name = name
        self.description = "测试工具"
        self.inputSchema = schema


def test_render_tool_params_translates_input_schema():
    tool = _FakeTool(
        "submit_demo",
        {
            "type": "object",
            "properties": {
                "reportName": {"type": "string", "description": "报告名称"},
                "summary": {"type": "string", "description": "摘要"},
            },
            "required": ["reportName"],
        },
    )

    params = mcp_client.render_tool_params(tool)

    assert params.startswith('submit_demo:{"reportName"')
    assert '"reportName":"(报告名称，必填)"' in params
    assert '"summary":"(摘要，可选)"' in params
    assert "\n" not in params


def test_render_tool_params_requires_schema_properties():
    tool = _FakeTool("submit_demo", {"type": "object", "properties": {}})

    with pytest.raises(ValueError, match="没有属性"):
        mcp_client.render_tool_params(tool)


def test_upstream_config_requires_existing_file(monkeypatch, work_tmp_dir):
    missing = work_tmp_dir / "config.json"
    monkeypatch.setenv(upstream_cfg.CONFIG_FILE_ENV, str(missing))

    with pytest.raises(FileNotFoundError):
        upstream_cfg.load_config()


def test_upstream_config_rejects_missing_field(monkeypatch, work_tmp_dir):
    config_file = work_tmp_dir / "config.json"
    config_file.write_text(
        '{"facility_report": {"api_url": "http://a"},'
        ' "dailypatrol": {"api_url": "http://b", "timeout_seconds": 10, "secret_key": "k"},'
        ' "ai_judgment": {"api_url": "http://c", "timeout_seconds": 10}}',
        encoding="utf-8",
    )
    monkeypatch.setenv(upstream_cfg.CONFIG_FILE_ENV, str(config_file))

    with pytest.raises(ValueError, match="facility_report"):
        upstream_cfg.load_config()


def test_upstream_config_env_override(monkeypatch, work_tmp_dir):
    config_file = work_tmp_dir / "config.json"
    config_file.write_text(
        '{"facility_report": {"api_url": "http://a", "timeout_seconds": 10},'
        ' "dailypatrol": {"api_url": "http://b", "timeout_seconds": 10, "secret_key": "k"},'
        ' "ai_judgment": {"api_url": "http://c", "timeout_seconds": 10}}',
        encoding="utf-8",
    )
    monkeypatch.setenv(upstream_cfg.CONFIG_FILE_ENV, str(config_file))
    monkeypatch.setenv("AGENTB_MCP_DAILYPATROL_SECRET_KEY", "override-key")
    monkeypatch.setenv("AGENTB_MCP_AI_JUDGMENT_TIMEOUT_SECONDS", "77")

    data = upstream_cfg.load_config()

    assert data["dailypatrol"]["secret_key"] == "override-key"
    assert data["ai_judgment"]["timeout_seconds"] == 77
    assert upstream_cfg.tool_config("submit_facility_forecast")["api_url"] == "http://a"


def test_upstream_tools_are_not_builtin_anymore():
    for name in sorted(MCP_TOOLS):
        assert name not in ALL_TOOLS, f"{name} 应改由 MCP 服务提供"


def test_mcp_health_alert_message_and_failure_count():
    mcp_health.reset()
    assert mcp_health.alert_message() is None

    first = mcp_health.record_failure("connect refused")
    second = mcp_health.record_failure("connect refused")

    assert first == 1 and second == 2
    assert mcp_health.snapshot()["available"] is False
    assert "connect refused" in mcp_health.alert_message()

    mcp_health.record_success()
    assert mcp_health.alert_message() is None
    mcp_health.reset()


def _fake_queue_recorder(monkeypatch, published):
    class _FakeQueue:
        def publish_sync(self, message):
            published.append(message)
            return True

    import singleton as singleton_module

    monkeypatch.setattr(singleton_module, "get_message_queue", lambda: _FakeQueue())


def test_execute_mcp_tool_pushes_alert_on_service_failure(monkeypatch):
    published = []

    monkeypatch.setattr(
        mcp_client,
        "execute_mcp_tool",
        lambda name, args: {
            "result": None,
            "error": "MCP 服务不可用：连接失败",
            "mcp_service_error": True,
        },
    )
    _fake_queue_recorder(monkeypatch, published)
    mcp_health.reset()
    mcp_health.record_failure("boom")

    result = tool_executor._execute_mcp_tool(
        "submit_ai_judgment_issue",
        {"facilityId": "1"},
        {"conversation_id": "conv-1", "session_id": "1", "workspace_id": "ws-1"},
    )

    assert "MCP 服务不可用" in result["error"]
    assert "mcp_service_error" not in result
    assert len(published) == 1
    assert published[0].type.value == "system_alert"
    assert published[0].metadata["source"] == "mcp"
    mcp_health.reset()


def test_execute_mcp_tool_does_not_push_alert_for_business_error(monkeypatch):
    """上游业务错误（如“用户不存在”）不属于 MCP 服务故障，不推 system_alert。"""
    published = []

    monkeypatch.setattr(
        mcp_client,
        "execute_mcp_tool",
        lambda name, args: {"result": None, "error": "提交失败: 用户不存在"},
    )
    _fake_queue_recorder(monkeypatch, published)

    result = tool_executor._execute_mcp_tool(
        "submit_ai_judgment_issue",
        {"facilityId": "1"},
        {"conversation_id": "conv-1", "session_id": "1", "workspace_id": "ws-1"},
    )

    assert result["error"] == "提交失败: 用户不存在"
    assert published == []


def test_execute_mcp_tool_resolves_report_file_path(monkeypatch):
    captured = {}

    class _FakeWorkspaceService:
        def resolve_path(self, workspace_id, relative_path):
            return True, str(Path("E:/ws") / workspace_id / relative_path)

    monkeypatch.setattr(
        mcp_client,
        "execute_mcp_tool",
        lambda name, args: captured.update(args) or {"result": "ok", "error": None},
    )

    tool_executor._execute_mcp_tool(
        "submit_facility_report",
        {"reportFile": "报告.docx"},
        {
            "conversation_id": "conv-1",
            "workspace_id": "ws-1",
            "workspace_service": _FakeWorkspaceService(),
        },
    )

    resolved = Path(captured["reportFile"])
    assert resolved.is_absolute()
    assert resolved.name == "报告.docx"


@pytest.mark.asyncio
async def test_mcp_server_exposes_four_tools_and_forwards_calls(monkeypatch):
    monkeypatch.setattr(
        upstream_server,
        "execute_submit_ai_judgment_issue",
        lambda args: {"result": f"submitted:{args['title']}", "error": None},
    )

    async with create_connected_server_and_client_session(upstream_server.mcp) as session:
        listed = await session.list_tools()
        tools = list(listed.tools)
        names = sorted(tool.name for tool in tools)
        assert names == sorted(mcp_client.configured_mcp_tools())

        for tool in tools:
            params = mcp_client.render_tool_params(tool)
            assert params.startswith(f"{tool.name}:{{")

        result = await session.call_tool(
            "submit_ai_judgment_issue",
            {
                "facilityId": "F1",
                "facilityName": "测试设施",
                "title": "裂缝",
                "regionId": "R1",
            },
        )

    payload = mcp_client._extract_payload(result)
    assert payload == {"result": "submitted:裂缝", "error": None}
