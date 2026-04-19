import json
import sys
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import TimeoutError as FutureTimeoutError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from service.agent_service.graph.director_agent import (
    _build_tool_schema_prompt,
    create_analyze_node,
    create_execute_node,
    create_plan_node,
    create_decide_next_action_node,
)
from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools
from service.agent_service.tools.sql_tools import execute_sql_query
from service.session_service.conversation_service import ConversationService


class FakeLLMService:
    def __init__(self, chat_responses=None, structured_responses=None):
        self.chat_responses = list(chat_responses or [])
        self.structured_responses = list(structured_responses or [])
        self.chat_calls = []
        self.structured_calls = []

    def chat(self, messages, system_prompt=None, **kwargs):
        self.chat_calls.append({"messages": messages, "system_prompt": system_prompt, "kwargs": kwargs})
        if self.chat_responses:
            return self.chat_responses.pop(0)
        if self.structured_responses:
            return json.dumps(self.structured_responses.pop(0), ensure_ascii=False)
        raise AssertionError("No fake chat response configured")

    def structured_output(self, messages, schema, system_prompt=None, **kwargs):
        self.structured_calls.append({"messages": messages, "schema": schema, "system_prompt": system_prompt, "kwargs": kwargs})
        if not self.structured_responses:
            raise AssertionError("No fake structured response configured")
        payload = self.structured_responses.pop(0)
        return schema(**payload)


class DummySettingsService:
    def get(self, key):
        if key == "agent:plan_auto_approve":
            return True
        raise KeyError(key)


def _base_state(user_message="实现登录功能"):
    return {
        "messages": [user_message],
        "workspace_id": "ws-1",
        "plan": [],
        "current_step": 0,
        "results": [],
        "plan_failed": False,
        "explore_result": None,
        "tool_history": [],
        "replan_count": 0,
        "agent_type": "director_agent",
        "is_root_graph": True,
        "intent_analysis": None,
        "parent_chain_messages": [],
        "current_conversation_messages": [],
        "execution_mode": None,
        "mode_reason": None,
        "suggested_tools": [],
        "in_plan_mode": False,
        "pending_tools": [],
        "has_tool_use": False,
        "final_reply": None,
        "plan_file": None,
        "last_tool_result": None,
        "last_tool_name": None,
        "last_tool_success": None,
        "last_tool_error": None,
        "iteration_count": 0,
        "max_iterations": 8,
        "current_step_goal": None,
        "current_step_done_when": None,
        "current_step_iteration_count": 0,
        "step_max_iterations": 8,
        "step_status": None,
        "replan_reason": None,
        "todos": [],
        "current_todo_index": 0,
        "current_todo_goal": None,
        "current_todo_done_when": None,
        "current_todo_iteration_count": 0,
        "todo_max_iterations": 8,
        "todo_status": None,
        "next_action": None,
    }


def test_plan_node_generates_outline_without_fixed_tools():
    llm = FakeLLMService(
        chat_responses=[json.dumps({
            "tasks": [
                {
                    "description": "梳理登录接口与表单字段",
                    "goal": "明确接口和页面的输入输出",
                    "done_when": "登录接口字段和前端表单字段均已明确",
                    "phase": "synthesis",
                },
                {
                    "description": "实现后端登录校验",
                    "goal": "后端能验证用户名密码",
                    "done_when": "无效凭证被拒绝且有效凭证返回成功",
                    "phase": "implementation",
                },
            ]
        })]
    )
    node = create_plan_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})
    state = _base_state()
    result = node(state)

    assert len(result["plan"]) == 2
    assert result["plan"][0]["description"] == "梳理登录接口与表单字段"
    assert result["plan"][0]["goal"] == "明确接口和页面的输入输出"
    assert result["plan"][0]["done_when"] == "登录接口字段和前端表单字段均已明确"
    assert result["plan"][0]["tool"] is None
    assert result["plan"][0]["args"] is None
    assert result["plan_file"]
    assert "plan.md" in result["final_reply"]


def test_direct_decide_can_work_against_current_todo():
    llm = FakeLLMService(
        chat_responses=[
            json.dumps({
                "kind": "tool",
                "tool_name": "read_file",
                "tool_args": {"file_path": "spec.txt"},
                "task_description": "读取规格文件",
            }, ensure_ascii=False),
            json.dumps({
                "kind": "step_done",
                "reply": "当前 todo 已完成",
            }, ensure_ascii=False),
        ]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state()
    state["execution_mode"] = "DIRECT"
    state["todos"] = ["读取规格文件"]
    state["current_todo_goal"] = "拿到规格内容"
    state["current_todo_done_when"] = "规格内容已确认"

    first = node(state)
    assert first["pending_tools"][0]["tool"] == "read_file"
    assert first["pending_tools"][0]["args"] == {"file_path": "spec.txt"}

    state["last_tool_result"] = "文件共 1 行，已读取全部内容\n\n1\t需求定义"
    state["tool_history"] = [{"tool": "read_file", "args": {"file_path": "spec.txt"}, "result": state["last_tool_result"]}]

    second = node(state)
    assert second["todo_status"] == "step_done"


def test_tool_schema_prompt_comes_from_registry_metadata():
    prompt = _build_tool_schema_prompt(["read_file", "update_todo", "list_workspace_files", "rag_search", "read_document"])

    assert 'read_file:{"file_path":"(文件路径)","start_line":"(第几行开始读，本参数可不填)","end_line":"(第几行结束读，本参数可不填)"}' in prompt
    assert 'update_todo:{"todos": ["(todo内容1)", "(todo内容2)"...],"doingIdx": (当前todo进行到第几项了，从0开始数)}' in prompt
    assert 'list_workspace_files:{}' in prompt
    assert 'rag_search:{"query":"(查询内容)","kb_ids":"(知识库ID列表，本参数可不填)","top_k":"(返回条数，本参数可不填)","min_score":"(最低相关度，本参数可不填)"}' in prompt
    assert 'read_document:{"file_path":"(文档路径)","start_idx":"(起始索引，从第几个字符开始读，默认0)","max_length":"(最大读取字符数，默认10000)","include_metadata":"(是否包含元数据，默认true)"}' in prompt


def test_default_tool_permissions_include_rag_search_for_director_and_plan():
    class PermissionSettingsService:
        def get(self, key):
            if key == "tool_permissions":
                return {
                    "director_agent": {"allowed": ["read_file", "rag_search"]},
                    "plan_agent": {"allowed": ["read_file", "rag_search"]},
                }
            raise KeyError(key)

    settings = PermissionSettingsService()

    assert "rag_search" in get_allowed_tools("director_agent", settings)
    assert "rag_search" in get_allowed_tools("plan_agent", settings)


def test_default_tool_permissions_include_sql_query_for_director_and_plan():
    class PermissionSettingsService:
        def get(self, key):
            if key == "tool_permissions":
                return {
                    "director_agent": {"allowed": ["read_file", "sql_query"]},
                    "plan_agent": {"allowed": ["read_file", "sql_query"]},
                }
            raise KeyError(key)

    settings = PermissionSettingsService()

    assert "sql_query" in get_allowed_tools("director_agent", settings)
    assert "sql_query" in get_allowed_tools("plan_agent", settings)


def test_execute_sql_query_returns_clear_error_for_unknown_database():
    result = execute_sql_query({
        "query": "SELECT 1",
        "database": "missing_db",
        "limit": "bad",
    })

    assert result["result"] is None
    assert "未找到数据库配置" in result["error"]
    assert "missing_db" in result["error"]


def test_conversation_service_creates_followup_for_auto_approved_plan():
    service = object.__new__(ConversationService)
    service._conversations = {}
    service._lock = __import__("asyncio").Lock()
    service._write_content_record = lambda *args, **kwargs: None

    class DaoStub:
        async def get_conversation_by_id(self, _conversation_id):
            return type("PersistedConversation", (), {"session_id": 42})()

    service._dao = DaoStub()

    async def run_case():
        with patch.object(service, "_is_plan_auto_approve_enabled", return_value=True), \
             patch.object(service, "get_conversation", return_value={
                 "assistant_content": json.dumps([
                     {"type": "plan_start", "content": "", "metadata": {}},
                     {"type": "text_delta", "content": "如果你同意方案，请直接回复“可以”或“同意方案”", "metadata": {}},
                     {"type": "done", "content": "", "metadata": {}},
                 ], ensure_ascii=False)
             }), \
             patch.object(service, "create_conversation", return_value="next-conv-1"):
            return await service._create_auto_approved_followup_conversation(
                "conv-1",
                final_reply="已生成方案，计划文件为 plan.md。\n如果你同意方案，请直接回复“可以”或“同意方案”，我会读取 plan.md 并严格按照方案继续执行。",
                session_id=42,
            )

    result = __import__("asyncio").run(run_case())

    assert result == {
        "event": "plan_auto_approved",
        "plan_status": "auto_approved",
        "approval_message": "可以",
        "next_conversation_id": "next-conv-1",
    }


    node = create_analyze_node(_llm_service=None, message_context={}, _settings_service=DummySettingsService())

    result = node(_base_state("探索这个项目的代码结构"))

    assert str(result["execution_mode"]).upper().endswith("DIRECT")
    assert result["has_tool_use"] is False
    assert result["pending_tools"] == []


def test_execute_rag_search_uses_real_executor_instead_of_fallback_success():
    node = create_execute_node(llm_service=None, settings_service=DummySettingsService(), message_context={})

    state = _base_state("从知识库检索登录方案")
    state["execution_mode"] = "DIRECT"
    state["pending_tools"] = [{"tool": "rag_search", "args": {"query": "登录方案"}}]

    with patch("service.agent_service.graph.subgraphs.tool_executor.execute_rag_search", return_value={"result": "mock rag result", "error": None}) as mock_rag:
        result = node(state)

    mock_rag.assert_called_once_with({"query": "登录方案"})
    assert result["last_tool_name"] == "rag_search"
    assert result["last_tool_success"] is True
    assert result["last_tool_result"] == "mock rag result"
    assert "工具 rag_search 执行成功" not in result["last_tool_result"]


def test_run_tool_execution_returns_failure_on_timeout():
    from service.agent_service.graph.subgraphs import tool_executor as module

    with patch.object(module, "create_tool_execution_subgraph") as mock_create:
        mock_graph = mock_create.return_value
        mock_graph.invoke = lambda state: {"result": "never returned", "error": None}

        class FakeFuture:
            def result(self, timeout=None):
                raise FutureTimeoutError()

        class FakeExecutor:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def submit(self, fn, *args, **kwargs):
                return FakeFuture()

        with patch.object(module, "ThreadPoolExecutor", return_value=FakeExecutor()):
            result = module.run_tool_execution(
                tool_name="read_document",
                tool_args={"file_path": "demo.docx"},
                workspace_id="ws-1",
                agent_type="director_agent",
                message_context={},
            )

    assert result["result"] is None
    assert "执行超时" in result["error"]


def test_run_tool_execution_returns_failure_on_unexpected_exception():
    from service.agent_service.graph.subgraphs import tool_executor as module

    with patch.object(module, "create_tool_execution_subgraph") as mock_create:
        mock_graph = mock_create.return_value
        mock_graph.invoke = lambda state: {"result": "never returned", "error": None}

        class FakeFuture:
            def result(self, timeout=None):
                raise RuntimeError("boom")

        class FakeExecutor:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def submit(self, fn, *args, **kwargs):
                return FakeFuture()

        with patch.object(module, "ThreadPoolExecutor", return_value=FakeExecutor()):
            result = module.run_tool_execution(
                tool_name="read_document",
                tool_args={"file_path": "demo.docx"},
                workspace_id="ws-1",
                agent_type="director_agent",
                message_context={},
            )

    assert result["result"] is None
    assert "执行异常" in result["error"]


def test_execute_read_document_structured_result_does_not_fail_tool_event_summary():
    node = create_execute_node(llm_service=None, settings_service=DummySettingsService(), message_context={})

    state = _base_state("读取测试 DOCX 文档")
    state["execution_mode"] = "DIRECT"
    state["pending_tools"] = [{"tool": "read_document", "args": {"file_path": "测试 DOCX 文档.docx"}}]

    structured_result = {
        "result": {
            "content": "文档内容摘要",
            "metadata": {"file_type": "docx"},
            "total_length": 6,
            "read_range": "0-6",
            "truncated": False,
        },
        "error": None,
    }

    with patch("service.agent_service.graph.subgraphs.tool_executor.execute_read_document", return_value=structured_result):
        with patch("service.agent_service.graph.subgraphs.tool_executor.FILE_TOOLS", set()):
            result = node(state)

    assert result["last_tool_name"] == "read_document"
    assert result["last_tool_success"] is True
    assert "dict' object has no attribute 'split" not in (result["last_tool_error"] or "")


def test_execute_update_todo_resets_direct_iteration_counters():
    node = create_execute_node(llm_service=None, settings_service=DummySettingsService(), message_context={})

    state = _base_state("读取 a.md")
    state["execution_mode"] = "DIRECT"
    state["iteration_count"] = 4
    state["current_todo_iteration_count"] = 2
    state["pending_tools"] = [{"tool": "update_todo", "args": {"todos": ["列出文件", "读取文件", "总结内容"], "doingIdx": 1}}]

    result = node(state)

    assert result["iteration_count"] == 0
    assert result["current_todo_iteration_count"] == 0
    assert result["todo_status"] == "pending"
    assert result["last_tool_result"] == '{"todos": ["列出文件", "读取文件", "总结内容"], "doingIdx": 1}'


def test_direct_invalid_tool_decision_retries_with_same_prompt_up_to_three_times():
    llm = FakeLLMService(
        chat_responses=[json.dumps({"kind": "tool"}, ensure_ascii=False)]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state("读取 a.md")
    state["execution_mode"] = "DIRECT"

    result = node(state)

    assert result["pending_tools"] == []
    assert result["final_reply"] is None
    assert result["invalid_tool_retry_count"] == 1


def test_direct_invalid_tool_decision_fails_after_three_retries():
    llm = FakeLLMService(
        chat_responses=[json.dumps({"kind": "tool"}, ensure_ascii=False)]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state("读取 a.md")
    state["execution_mode"] = "DIRECT"
    state["invalid_tool_retry_count"] = 3

    result = node(state)

    assert "工具决策无效" in result["final_reply"]
    assert result["invalid_tool_retry_count"] == 4


def test_direct_system_prompt_requires_complete_tool_json_shape():
    llm = FakeLLMService(
        chat_responses=[
            json.dumps({
                "kind": "tool",
                "tool_name": "read_file",
                "tool_args": {"file_path": "a.md"},
                "task_description": "读取 a.md",
            }, ensure_ascii=False)
        ]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state("读取 a.md 并总结其中要点")
    state["execution_mode"] = "DIRECT"

    node(state)
    system_prompt = llm.chat_calls[-1]["system_prompt"]

    assert '"kind": "tool"' in system_prompt
    assert '"tool_name": "工具名"' in system_prompt
    assert '"tool_args": {"参数名": "参数值"}' in system_prompt
    assert '"task_description": "这一步要做什么"' in system_prompt
    assert '如果拿不准下一步该用什么工具或缺少必填参数，返回 blocked' in system_prompt


def test_direct_prompt_uses_single_tool_schema_section():
    llm = FakeLLMService(
        chat_responses=[
            json.dumps({
                "kind": "tool",
                "tool_name": "read_file",
                "tool_args": {"file_path": "a.md"},
                "task_description": "读取 a.md",
            }, ensure_ascii=False)
        ]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state("读取 a.md 并总结其中要点")
    state["execution_mode"] = "DIRECT"

    node(state)
    prompt = llm.chat_calls[-1]["messages"][0]["content"]

    assert "工具列表：" in prompt
    assert "可用工具:" not in prompt


def test_direct_prompt_omits_todo_block_when_empty():
    llm = FakeLLMService(
        chat_responses=[
            json.dumps({
                "kind": "tool",
                "tool_name": "read_file",
                "tool_args": {"file_path": "a.md"},
                "task_description": "读取 a.md",
            }, ensure_ascii=False)
        ]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state("读取 a.md 并总结其中要点")
    state["execution_mode"] = "DIRECT"

    result = node(state)
    prompt = llm.chat_calls[-1]["messages"][0]["content"]

    assert "当前 TODO 列表（完整状态）" not in prompt
    assert result["pending_tools"][0]["tool"] == "read_file"


    llm = FakeLLMService(
        structured_responses=[
            {
                "kind": "tool",
                "tool_name": "update_todo",
                "tool_args": {"todos": ["阅读 a.md", "重构 a.md", "写重构结果"], "doingIdx": 1},
                "task_description": "更新当前 todo 列表",
            }
        ]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state()
    state["execution_mode"] = "DIRECT"
    state["todos"] = ["阅读 a.md", "重构 a.md", "写重构结果"]
    state["current_todo_index"] = 1
    state["current_todo_goal"] = "完成重构"
    state["current_todo_done_when"] = "重构后的内容已落地"

    result = node(state)
    prompt = llm.chat_calls[-1]["messages"][0]["content"]

    assert "当前 TODO 列表（完整状态）" in prompt
    assert "- [0] 阅读 a.md" in prompt
    assert "- [1] 重构 a.md <= 当前执行项" in prompt
    assert "- [2] 写重构结果" in prompt
    assert result["pending_tools"][0]["tool"] == "update_todo"


def test_direct_can_choose_explicit_update_todo_for_completion():
    llm = FakeLLMService(
        chat_responses=[
            json.dumps({
                "kind": "tool",
                "tool_name": "update_todo",
                "tool_args": {"todos": ["读取规格文件"], "doingIdx": 0},
                "task_description": "重写 todo 列表以反映当前状态",
            }, ensure_ascii=False)
        ]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state()
    state["execution_mode"] = "DIRECT"
    state["todos"] = ["读取规格文件"]
    state["current_todo_index"] = 0
    state["current_todo_goal"] = "拿到规格内容"
    state["current_todo_done_when"] = "规格内容已确认"
    state["last_tool_result"] = "文件共 1 行，已读取全部内容\n\n1\t需求定义"
    state["tool_history"] = [{"tool": "read_file", "args": {"file_path": "spec.txt"}, "result": state["last_tool_result"]}]

    result = node(state)
    assert result["pending_tools"][0]["tool"] == "update_todo"
    assert result["pending_tools"][0]["args"] == {"todos": ["读取规格文件"], "doingIdx": 0}
