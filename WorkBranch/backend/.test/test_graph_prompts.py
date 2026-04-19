import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from service.agent_service.prompts.graph_prompts import (
    build_chat_system_prompt,
    build_context_prompt,
    build_plan_generation_messages,
    build_special_tool_prompt,
    build_tool_schema_prompt,
)


class VisionSettingsService:
    def get(self, key):
        if key == "llm:supports_vision":
            return True
        raise KeyError(key)


class ToolPermissionSettingsService:
    def get(self, key):
        if key == "tool_permissions":
            return {
                "director_agent": {
                    "allowed": ["read_file", "update_todo", "rag_search"]
                }
            }
        raise KeyError(key)


def test_build_context_prompt_keeps_history_order():
    prompt = build_context_prompt(
        parent_chain_messages=[{"role": "user", "content": "旧问题"}],
        current_conversation_messages=[{"role": "assistant", "content": "当前回答"}],
        current_task="执行当前任务",
    )

    assert "[历史对话]" in prompt
    assert "user: 旧问题" in prompt
    assert "[当前对话内历史]" in prompt
    assert "assistant: 当前回答" in prompt
    assert prompt.endswith("执行当前任务")


def test_build_chat_system_prompt_appends_native_multimodal_note():
    prompt = build_chat_system_prompt(VisionSettingsService())

    assert "支持图像理解" in prompt
    assert "不要声称缺少图像工具" in prompt


def test_build_tool_schema_prompt_uses_registry_metadata():
    prompt = build_tool_schema_prompt(["read_file", "update_todo", "rag_search", "sql_query"])

    assert 'read_file:{"file_path":"(文件路径)"' in prompt
    assert 'update_todo:{"todos": ["(todo内容1)", "(todo内容2)"...],"doingIdx": (当前todo进行到第几项了，从0开始数)}' in prompt
    assert 'rag_search:{"query":"(查询内容)"' in prompt
    assert 'sql_query:{"mode":"(query|show_databases|show_tables|describe|show_create，必填)","query":"(query 模式必填；其他模式忽略)","database":"(数据库名称，可选；show_databases 模式忽略，show_tables/describe/show_create 使用该库或默认库)","table":"(表名；describe/show_create 模式必填，其他模式忽略)","limit":"(仅 query 模式生效，默认100，最大1000)"}' in prompt


def test_build_plan_generation_messages_include_intent_context():
    system_prompt, messages = build_plan_generation_messages(
        user_message="优化提示词结构",
        parent_chain_messages=[{"role": "user", "content": "之前讨论"}],
        current_conversation_messages=[{"role": "assistant", "content": "当前上下文"}],
        intent_analysis={
            "intent_type": "refactor",
            "summary": "抽离 prompt builder",
            "key_points": ["拆分文件", "保持兼容"],
            "suggested_tools": ["read_file"],
            "complexity": "medium",
        },
        agent_type="director_agent",
        settings_service=ToolPermissionSettingsService(),
    )

    assert "工具列表：" in system_prompt
    assert "只输出 JSON" in system_prompt
    assert len(messages) == 1
    assert "## 意图分析结果" in messages[0]["content"]
    assert "抽离 prompt builder" in messages[0]["content"]
    assert "请根据以上用户当前问题生成执行计划" in messages[0]["content"]


def test_build_special_tool_prompt_includes_previous_results():
    prompt = build_special_tool_prompt(
        task_description="整理结果",
        previous_results=["第一步成功", "第二步成功"],
        final_instruction="请向用户输出回复。",
    )

    assert "当前任务: 整理结果" in prompt
    assert "--- 之前任务的执行结果 ---" in prompt
    assert "任务1结果:\n第一步成功" in prompt
    assert prompt.endswith("请向用户输出回复。")
