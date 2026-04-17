import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from service.agent_service.graph.director_agent import (
    _build_tool_schema_prompt,
    _hydrate_todos_from_plan,
    create_analyze_node,
    create_execute_node,
    create_plan_node,
    create_decide_next_action_node,
    create_replan_remaining_steps_node,
)


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
    assert str(result["execution_mode"]).upper().endswith("DIRECT")
    assert result["todos"][0] == "梳理登录接口与表单字段"
    assert result["current_todo_goal"] is None
    assert result["current_todo_done_when"] is None


def test_plan_markdown_can_hydrate_todos():
    plan_md = """# 执行计划

## 执行步骤

1. **读取规格文件**
   - 目标: `拿到规格内容`
   - 完成条件: `规格内容已确认`
   - 工具: `None`

2. **生成输出文件**
   - 目标: `写出结果文件`
   - 完成条件: `输出文件已经存在`
   - 工具: `None`
"""
    todos = _hydrate_todos_from_plan(plan_md, "ws-plan-test")
    assert len(todos) == 2
    assert todos == ["读取规格文件", "生成输出文件"]


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
    prompt = _build_tool_schema_prompt(["read_file", "update_todo", "list_workspace_files"])

    assert 'read_file:{"file_path":"(文件路径)","start_line":"(第几行开始读，本参数可不填)","end_line":"(第几行结束读，本参数可不填)"}' in prompt
    assert 'update_todo:{"todos": ["(todo内容1)", "(todo内容2)"...],"doingIdx": (当前todo进行到第几项了，从0开始数)}' in prompt
    assert 'list_workspace_files:{}' in prompt


def test_analyze_node_downgrades_legacy_subagent_mode_to_direct():
    llm = FakeLLMService(
        chat_responses=[json.dumps({
            "complexity": "medium",
            "intent_type": "explore",
            "execution_mode": "SUBAGENT",
            "reason": "旧模式输出",
            "suggested_agent": "explore_agent",
        }, ensure_ascii=False)]
    )
    node = create_analyze_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    result = node(_base_state("探索这个项目的代码结构"))

    assert str(result["execution_mode"]).upper().endswith("DIRECT")
    assert result["has_tool_use"] is True
    assert "suggested_subagent" not in result
    assert "active_subagent" not in result


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
    llm = FakeLLMService(
        chat_responses=[json.dumps({
            "tasks": [
                {
                    "description": "补充剩余后端实现",
                    "goal": "完成剩余后端逻辑",
                    "done_when": "剩余后端改动完成",
                    "phase": "implementation",
                },
                {
                    "description": "补充验证",
                    "goal": "确认修复有效",
                    "done_when": "验证通过",
                    "phase": "verification",
                },
            ]
        })]
    )
    node = create_replan_remaining_steps_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state()
    state["plan"] = [
        {"id": 1, "description": "完成需求分析", "goal": "明确范围", "done_when": "范围明确", "phase": "research", "status": "completed", "tool": None, "args": None, "result": "ok", "feedback": None},
        {"id": 2, "description": "旧实现步骤", "goal": "旧目标", "done_when": "旧完成条件", "phase": "implementation", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
    ]
    state["todos"] = ["完成需求分析", "旧实现步骤"]
    state["current_todo_index"] = 1
    state["replan_reason"] = "原步骤受阻"

    result = node(state)
    assert len(result["plan"]) == 3
    assert result["plan"][0]["description"] == "完成需求分析"
    assert result["plan"][1]["description"] == "补充剩余后端实现"
    assert result["current_todo_index"] == 1
    assert result["current_todo_goal"] is None
    assert result["replan_reason"] is None
