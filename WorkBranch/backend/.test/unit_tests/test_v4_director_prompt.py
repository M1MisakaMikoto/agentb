import os
import sys


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.v4 import prompt
from service.agent_service.graph.subgraphs import tool_registry
from service.agent_service.prompts import graph_prompts


def test_director_prompt_keeps_prediction_and_guides_document_reading(
    monkeypatch,
):
    visible_tools = []

    monkeypatch.setattr(
        tool_registry,
        "get_allowed_tools",
        lambda *_args, **_kwargs: [
            "thinking",
            "call_explore_agent",
            "call_review_agent",
            "call_prediction_agent",
            "call_plan_agent",
            "document",
            "write_file",
        ],
    )

    def capture_schema(tools, agent_type):
        visible_tools.extend(tools)
        return f"schema for {agent_type}: {','.join(tools)}"

    monkeypatch.setattr(graph_prompts, "build_tool_schema_prompt", capture_schema)

    system_prompt, _ = prompt.build_tagged_prompt(
        agent_type="director_agent",
        user_message="分析三个监测报告",
        workspace_id="workspace-1",
        round_no=1,
        max_iterations=32,
        tool_records=[],
        todos=[],
        current_todo_index=0,
        plan_content=None,
        parent_chain_messages=[],
        current_conversation_messages=[],
    )

    assert visible_tools == ["call_prediction_agent", "document", "write_file"]
    assert prompt.V4_DIRECTOR_EXECUTION_PROMPT in system_prompt
    assert prompt.V4_DOCUMENT_READING_PROMPT in system_prompt
    assert "call_prediction_agent" in prompt.V4_DIRECTOR_EXECUTION_PROMPT

    assert prompt.V4_SKILL_USAGE_PROMPT in system_prompt
    assert "skill 工具的 list 操作" in system_prompt
    assert "必须先调用 skill 的 read 操作读取其内容" in system_prompt
    assert "如果在当前工具协议里不存在" in system_prompt
    assert "不要臆造工具名" in system_prompt
    assert "上游工具 MCP 服务" in system_prompt
    # 地区数据库规则已迁入技能（WorkBranch/skills/region-database-rules），不再写死在提示词里
    assert "TB_Market" not in prompt.V4_DIRECTOR_EXECUTION_PROMPT
    assert "TB_Market" not in prompt.V4_DOCUMENT_READING_PROMPT
    assert "根据文件大小" in system_prompt
    assert "阅读文档时先读开头" in system_prompt
    assert "必要时再读末尾（开头更重要）" in system_prompt
    assert "搜索只能用于定位信息" in system_prompt
    assert "命中后应使用 read_hint 调用 r" in system_prompt
    assert "扩展读取相关段落或章节" in system_prompt
    assert "仅在继续相同 pattern 时" in system_prompt
    assert "read_hint" in system_prompt
    assert "occurrences" in system_prompt
    assert "next_start_idx" in system_prompt
    assert "不以命中数、返回数或文本长度作为依据" in system_prompt
    assert "若缺失信息会影响结论则继续查，否则立即推进工作" in system_prompt
    assert "优先使用搜索类工具" not in system_prompt
    assert "只有片段缺少所需上下文时" not in system_prompt

    assert prompt.V4_REPORT_PROVENANCE_PROMPT in system_prompt
    assert prompt.V4_KNOWLEDGE_SOURCE_PROMPT in system_prompt
    assert "## 数据来源与工作流程" in system_prompt
    assert "## 结论依据说明" in system_prompt
    assert "3-6 条要点" in system_prompt
    assert "标准等信息基于模型知识" in system_prompt
    assert "标准等信息综合模型知识和知识库" in system_prompt
    assert "标准等信息基于知识库" in system_prompt
    assert "知识库中未找到相关内容" in system_prompt
    assert "以是否成功检索到内容为准" in system_prompt
    assert "数据库的报错行为保持不变" in system_prompt
    assert "不罗列工具名、参数与调用链" in system_prompt

    assert prompt.V4_BRIDGE_ONLY_SCOPE_PROMPT in system_prompt
    assert "暂不支持隧道" in system_prompt
    assert "不得给出隧道技术状况评分" in system_prompt


def test_non_director_tool_schema_is_not_filtered(monkeypatch):
    visible_tools = []
    allowed = ["thinking", "call_explore_agent", "document"]
    monkeypatch.setattr(
        tool_registry,
        "get_allowed_tools",
        lambda *_args, **_kwargs: allowed,
    )

    def capture_schema(tools, agent_type):
        visible_tools.extend(tools)
        return f"schema for {agent_type}"

    monkeypatch.setattr(graph_prompts, "build_tool_schema_prompt", capture_schema)

    prompt.build_tagged_prompt(
        agent_type="explore_agent",
        user_message="探索",
        workspace_id="workspace-1",
        round_no=1,
        max_iterations=8,
        tool_records=[],
        todos=[],
        current_todo_index=0,
        plan_content=None,
        parent_chain_messages=[],
        current_conversation_messages=[],
    )

    assert visible_tools == allowed


def test_prediction_prompt_includes_document_reading_guidance():
    system_prompt, _ = prompt.build_tagged_prompt(
        agent_type="prediction_agent",
        user_message="predict bridge trend",
        workspace_id="workspace-1",
        round_no=1,
        max_iterations=8,
        tool_records=[],
        todos=[],
        current_todo_index=0,
        plan_content=None,
        parent_chain_messages=[],
        current_conversation_messages=[],
    )
    assert prompt.V4_DOCUMENT_READING_PROMPT in system_prompt
    assert prompt.V4_DIRECTOR_EXECUTION_PROMPT not in system_prompt
    assert prompt.V4_REPORT_PROVENANCE_PROMPT in system_prompt
    assert prompt.V4_KNOWLEDGE_SOURCE_PROMPT in system_prompt
    assert prompt.V4_SKILL_USAGE_PROMPT in system_prompt
    assert prompt.V4_BRIDGE_ONLY_SCOPE_PROMPT in system_prompt
    assert "暂不支持隧道" in system_prompt


def test_prediction_agent_prompt_declares_bridge_only_scope():
    from service.agent_service.prompts.agent_prompts import PREDICTION_AGENT_PROMPT

    assert "只处理桥梁" in PREDICTION_AGENT_PROMPT
    assert "隧道暂不支持" in PREDICTION_AGENT_PROMPT


def test_report_provenance_guidance_is_limited_to_report_producing_agents():
    system_prompt, _ = prompt.build_tagged_prompt(
        agent_type="explore_agent",
        user_message="读取检测报告",
        workspace_id="workspace-1",
        round_no=1,
        max_iterations=8,
        tool_records=[],
        todos=[],
        current_todo_index=0,
        plan_content=None,
        parent_chain_messages=[],
        current_conversation_messages=[],
    )
    assert prompt.V4_REPORT_PROVENANCE_PROMPT not in system_prompt
    assert prompt.V4_KNOWLEDGE_SOURCE_PROMPT not in system_prompt
    assert prompt.V4_SKILL_USAGE_PROMPT in system_prompt


def test_service_alert_block_absent_when_mcp_available(monkeypatch):
    from service.agent_service.tools import mcp_health

    mcp_health.reset()
    mcp_health.record_success()

    _, user_message = prompt.build_tagged_prompt(
        agent_type="director_agent",
        user_message="分析三个监测报告",
        workspace_id="workspace-1",
        round_no=1,
        max_iterations=32,
        tool_records=[],
        todos=[],
        current_todo_index=0,
        plan_content=None,
        parent_chain_messages=[],
        current_conversation_messages=[],
    )

    assert "<service_alert>" not in user_message
    mcp_health.reset()


def test_service_alert_block_present_when_mcp_unavailable(monkeypatch):
    from service.agent_service.tools import mcp_health

    mcp_health.reset()
    mcp_health.record_failure("ConnectError: All connection attempts failed")

    _, user_message = prompt.build_tagged_prompt(
        agent_type="director_agent",
        user_message="上传研判报告",
        workspace_id="workspace-1",
        round_no=1,
        max_iterations=32,
        tool_records=[],
        todos=[],
        current_todo_index=0,
        plan_content=None,
        parent_chain_messages=[],
        current_conversation_messages=[],
    )

    assert "<service_alert>" in user_message
    assert "上游工具 MCP 服务" in user_message
    assert "submit_facility_report" in user_message
    assert "ConnectError" in user_message
    mcp_health.reset()
