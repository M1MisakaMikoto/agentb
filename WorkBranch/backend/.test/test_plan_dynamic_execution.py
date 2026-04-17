import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from service.agent_service.graph.director_agent import (
    _hydrate_todos_from_plan,
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
        if not self.chat_responses:
            raise AssertionError("No fake chat response configured")
        return self.chat_responses.pop(0)

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
        "suggested_subagent": None,
        "in_plan_mode": False,
        "active_subagent": False,
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
    assert result["todos"][0]["description"] == "梳理登录接口与表单字段"
    assert result["current_todo_goal"] == "明确接口和页面的输入输出"
    assert result["current_todo_done_when"] == "登录接口字段和前端表单字段均已明确"


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
    assert todos[0]["description"] == "读取规格文件"
    assert todos[0]["goal"] == "拿到规格内容"
    assert todos[0]["done_when"] == "规格内容已确认"


def test_direct_decide_can_work_against_current_todo():
    llm = FakeLLMService(
        structured_responses=[
            {
                "kind": "tool",
                "tool_name": "read_file",
                "tool_args": {"file_path": "spec.txt"},
                "task_description": "读取规格文件",
            },
            {
                "kind": "step_done",
                "reply": "当前 todo 已完成",
            },
        ]
    )
    node = create_decide_next_action_node(llm_service=llm, settings_service=DummySettingsService(), message_context={})

    state = _base_state()
    state["execution_mode"] = "DIRECT"
    state["todos"] = [{
        "id": 1,
        "description": "读取规格文件",
        "goal": "拿到规格内容",
        "done_when": "规格内容已确认",
        "status": "pending",
        "result": None,
        "attempt_count": 0,
    }]
    state["current_todo_goal"] = "拿到规格内容"
    state["current_todo_done_when"] = "规格内容已确认"

    first = node(state)
    assert first["pending_tools"][0]["tool"] == "read_file"
    assert first["pending_tools"][0]["args"]["file_path"] == "spec.txt"

    state["last_tool_result"] = "文件共 1 行，已读取全部内容\n\n1\t需求定义"
    state["tool_history"] = [{"tool": "read_file", "args": {"file_path": "spec.txt"}, "result": state["last_tool_result"]}]

    second = node(state)
    assert second["todo_status"] == "step_done"


def test_replan_remaining_steps_only_rewrites_unfinished_steps():
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
    state["todos"] = [
        {"id": 1, "description": "完成需求分析", "goal": "明确范围", "done_when": "范围明确", "status": "completed", "result": "ok", "attempt_count": 0},
        {"id": 2, "description": "旧实现步骤", "goal": "旧目标", "done_when": "旧完成条件", "status": "pending", "result": None, "attempt_count": 0},
    ]
    state["current_todo_index"] = 1
    state["replan_reason"] = "原步骤受阻"

    result = node(state)
    assert len(result["plan"]) == 3
    assert result["plan"][0]["description"] == "完成需求分析"
    assert result["plan"][1]["description"] == "补充剩余后端实现"
    assert result["current_todo_index"] == 1
    assert result["current_todo_goal"] == "完成剩余后端逻辑"
    assert result["replan_reason"] is None
