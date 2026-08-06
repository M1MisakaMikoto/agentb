import os
import sys
import unittest
from unittest.mock import mock_open, patch

from pydantic import ValidationError


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.decision.response_schema import parse_decision_response
from service.agent_service.graph.react_agent_base import ReActAgentBase
from service.agent_service.prompts.error_injection import (
    create_json_format_error,
    format_error_for_prompt,
)
from service.agent_service.prompts.graph_prompts import generate_prompt


class _ResponseLLM:
    def __init__(self, response):
        self.response = response

    def chat_with_json_mode(self, **_kwargs):
        return self.response


def _base_state(**updates):
    state = {
        "user_message": "读取目标文件",
        "workspace_id": "workspace-test",
        "agent_type": "director_agent",
        "iteration_count": 0,
        "max_iterations": 10,
        "tool_history": [],
        "todos": [],
    }
    state.update(updates)
    return state


def _decision_node(response):
    agent = ReActAgentBase.__new__(ReActAgentBase)
    return agent._create_decide_node(
        llm_service=_ResponseLLM(response),
        settings_service=None,
        message_context=None,
    )


class DecisionResponseSchemaTest(unittest.TestCase):
    def test_accepts_all_decision_kinds_and_preserves_extra_fields(self):
        cases = [
            '{"kind":"tool","tool_name":"read_file","custom":1}',
            '{"kind":"step_done","reply":"extra is allowed"}',
            '{"kind":"blocked"}',
        ]

        parsed = [parse_decision_response(case) for case in cases]

        self.assertEqual([item["kind"] for item in parsed], ["tool", "step_done", "blocked"])
        self.assertEqual(parsed[0]["custom"], 1)
        self.assertEqual(parsed[1]["reply"], "extra is allowed")

    def test_rejects_non_object_responses(self):
        for response in ('[{}]', '[{"kind":"step_done"}]', 'null', '"step_done"', '1'):
            with self.subTest(response=response), self.assertRaises(ValidationError):
                parse_decision_response(response)

    def test_rejects_missing_or_unknown_kind(self):
        for response in ('{}', '{"kind":"reply"}', '{"kind":null}'):
            with self.subTest(response=response), self.assertRaises(ValidationError):
                parse_decision_response(response)

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValidationError):
            parse_decision_response('{bad')


class DecisionErrorPromptTest(unittest.TestCase):
    def test_formats_complete_decision_error_at_prompt_tail(self):
        original = '[{"kind":"step_done","payload":"不截断内容"}]'
        error = create_json_format_error(original, "完整校验错误")

        prompt = format_error_for_prompt(error)

        self.assertEqual(
            prompt,
            "⚠️ 上一次决策格式错误：\n"
            "- 错误详情: JSON 格式错误: 完整校验错误\n"
            f"- 原始JSON: {original}\n"
            "请返回合法的顶层JSON对象后重新决策。",
        )

    @patch("builtins.open", new_callable=mock_open)
    def test_active_node_injects_full_error_into_next_prompt(self, _open):
        original = '{"kind":"invalid_kind"}'

        result = _decision_node(original)(_base_state())
        _, next_prompt = generate_prompt(
            agent_type="director_agent",
            mode="DIRECT",
            user_message="读取目标文件",
            workspace_id="workspace-test",
            iteration_count=1,
            max_iterations=10,
            tool_schema_prompt="",
            tool_history=[],
            last_tool_result=None,
            todos=[],
            current_todo_index=0,
            last_error=result["last_error"],
        )

        self.assertEqual(result["decision_error_count"], 1)
        self.assertEqual(result["last_error"].original_json, original)
        self.assertTrue(next_prompt.endswith(format_error_for_prompt(result["last_error"])))
        self.assertIn(original, next_prompt)

    @patch("builtins.open", new_callable=mock_open)
    def test_active_node_terminates_with_full_third_response(self, _open):
        original = '{"kind":"bogus"}'

        result = _decision_node(original)(_base_state(decision_error_count=2))

        self.assertEqual(result["decision_error_count"], 3)
        self.assertIn(original, result["final_reply"])
        self.assertIn("原始响应", result["final_reply"])

    def test_child_route_retries_then_uses_error_summary(self):
        agent = ReActAgentBase.__new__(ReActAgentBase)

        self.assertEqual(agent._route_after_decide(_base_state(decision_error_count=1)), "decide")
        self.assertEqual(agent._route_after_decide(_base_state(decision_error_count=3)), "error_summary")


if __name__ == "__main__":
    unittest.main()
