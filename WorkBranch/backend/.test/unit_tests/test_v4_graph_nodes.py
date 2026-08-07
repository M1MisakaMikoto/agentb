import os
import sys
import unittest
from unittest.mock import patch


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.v4.acting import create_acting_node
from service.agent_service.graph.v4.closuring import create_closuring_node
from service.agent_service.graph.v4.graph import (
    build_v4_graph,
    resume_v4_graph,
    run_v4_graph,
)
from service.agent_service.graph.subgraphs.tool_executor import _execute_call_plan_agent
from service.agent_service.graph.v4.prompt import (
    build_current_task,
    build_tagged_prompt,
    format_tool_records,
)
from service.agent_service.graph.v4.reasoning import create_reasoning_node


def _base_state(**updates):
    state = {
        "messages": [{"role": "user", "content": "读取目标文件"}],
        "current_user_message_text": "读取目标文件",
        "workspace_id": "ws-v4-test",
        "agent_type": "director_agent",
        "iteration_count": 0,
        "max_iterations": 10,
        "tool_records": [],
        "todos": [],
        "current_todo_index": 0,
        "parent_chain_messages": [],
        "current_conversation_messages": [],
    }
    state.update(updates)
    return state


class _ResponseLLM:
    def __init__(self, response):
        self.response = response

    def chat_with_structured_output(self, **_kwargs):
        return self.response


class ReasoningNodeTest(unittest.TestCase):
    def _node(self, response):
        return create_reasoning_node(
            llm_service=_ResponseLLM(response),
            settings_service=None,
            message_context=None,
        )

    def test_tool_calls_routes_to_acting(self):
        raw = (
            '{"type":"tool_calls","content":{"reason":"并行","calls":['
            '{"call_seq":1,"tool_name":"read_file","tool_args":{"file_path":"a.txt"}}]}}'
        )
        result = self._node(raw)(_base_state())
        self.assertEqual(result["_route_target"], "acting")
        self.assertEqual(result["pending_batch"]["calls"][0]["tool_name"], "read_file")

    def test_text_routes_to_finalize_when_closuring_disabled(self):
        result = self._node('{"type":"text","content":"完成总结"}')(_base_state())
        self.assertEqual(result["_route_target"], "finalize")
        self.assertEqual(result["pending_final_text"], "完成总结")

    def test_parse_error_retries_inside_reasoning(self):
        result = self._node("这不是 JSON")(_base_state())
        self.assertEqual(result["_route_target"], "reasoning")
        self.assertEqual(result["decision_error_count"], 1)
        self.assertIn("json_syntax", result["parse_error"])
        self.assertIn("这不是 JSON", result["parse_error"])

    def test_semantic_error_retries_inside_reasoning(self):
        raw = (
            '{"type":"tool_calls","content":{"reason":"r","calls":['
            '{"call_seq":1,"tool_name":"not_a_tool","tool_args":{}}]}}'
        )
        result = self._node(raw)(_base_state())
        self.assertEqual(result["_route_target"], "reasoning")
        self.assertIn("semantic", result["parse_error"])

    def test_iteration_limit_terminates_with_fixed_template(self):
        state = _base_state(iteration_count=10, max_iterations=10)
        result = self._node("x")(state)
        self.assertEqual(result["_route_target"], "finalize")
        self.assertIn("已达最大轮次", result["pending_final_text"])

    def test_decision_error_limit_terminates(self):
        state = _base_state(
            decision_error_count=3,
            parse_error="反复失败",
            parse_error_raw='{"bad"',
        )
        result = self._node("x")(state)
        self.assertEqual(result["_route_target"], "finalize")
        self.assertIn("解析连续失败", result["pending_final_text"])
        self.assertIn('{"bad"', result["pending_final_text"])


class ActingNodeTest(unittest.TestCase):
    def _node(self):
        return create_acting_node(
            llm_service=None,
            settings_service=None,
            message_context=None,
            post_execute_hook=None,
        )

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        side_effect=lambda **kw: {
            "result": f"OK-{kw['tool_name']}",
            "error": None,
        },
    )
    def test_batch_parallel_writes_records(self, _mock):
        state = _base_state(
            pending_batch={
                "reason": "并行读取",
                "calls": [
                    {"call_seq": 1, "tool_name": "read_file", "tool_args": {"file_path": "a"}},
                    {"call_seq": 2, "tool_name": "list_dir", "tool_args": {}},
                ],
            }
        )
        result = self._node()(state)
        self.assertEqual(result["_route_target"], "reasoning")
        self.assertEqual(result["iteration_count"], 1)
        records = result["tool_records"]
        # 批次头 + 2 条记录
        self.assertEqual(records[0], {"round": 1, "reason": "并行读取"})
        call_records = [r for r in records if r.get("call_seq") is not None]
        self.assertEqual(len(call_records), 2)
        self.assertTrue(all(r["status"] == "success" for r in call_records))
        self.assertEqual(call_records[0]["call_seq"], 1)

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        side_effect=lambda **kw: {"result": None, "error": "连接失败"},
    )
    def test_failed_call_sets_acting_failures(self, _mock):
        state = _base_state(
            pending_batch={
                "reason": "r",
                "calls": [{"call_seq": 1, "tool_name": "sql_query", "tool_args": {}}],
            }
        )
        result = self._node()(state)
        self.assertEqual(result["acting_failures"][0]["status"], "failed")
        self.assertEqual(result["acting_failures"][0]["call_seq"], 1)

    def test_chat_tool_retired(self):
        state = _base_state(
            pending_batch={
                "reason": "r",
                "calls": [{"call_seq": 1, "tool_name": "chat", "tool_args": {}}],
            }
        )
        result = self._node()(state)
        self.assertEqual(result["acting_failures"][0]["status"], "failed")
        self.assertIn("已退役", result["acting_failures"][0]["error"])


class PromptTagTest(unittest.TestCase):
    def test_default_current_task_is_protocol(self):
        task = build_current_task()
        self.assertIn("输出协议", task)
        self.assertIn("tool_calls", task)

    def test_acting_failure_rewrites_current_task(self):
        task = build_current_task(acting_failures=[
            {"call_seq": 1, "tool_name": "sql_query", "status": "failed"},
        ])
        self.assertIn("总结", task)
        self.assertNotIn("输出协议", task)

    def test_tagged_prompt_contains_all_sections(self):
        system_prompt, user_message = build_tagged_prompt(
            agent_type="director_agent",
            user_message="问题",
            workspace_id="ws",
            round_no=1,
            max_iterations=10,
            tool_records=[
                {"round": 1, "reason": "r"},
                {"round": 1, "call_seq": 1, "tool_name": "read_file",
                 "status": "success", "result": "内容"},
            ],
            todos=["todo1"],
            current_todo_index=0,
            plan_content=None,
            parent_chain_messages=[],
            current_conversation_messages=[],
            parse_error="类别: json_syntax",
            closur_feedback="请补 text 总结",
        )
        for tag in [
            "<system>", "<current_task>", "<tool_records>",
            "<parse_error>", "<closur-feedback>", "<user_question>", "<todos>",
        ]:
            self.assertIn(tag, user_message)
        self.assertIn("tool_calls", system_prompt)

    def test_tool_records_grouping(self):
        text = format_tool_records([
            {"round": 2, "reason": "第二批"},
            {"round": 2, "call_seq": 1, "tool_name": "read_file",
             "status": "success", "result": "x"},
            {"round": 1, "call_seq": 1, "tool_name": "search_files",
             "status": "failed", "error": "err"},
        ])
        self.assertIn("round=1", text)
        self.assertIn("round=2", text)
        self.assertIn("status=failed", text)
        self.assertIn("error=err", text)


class _SeqLLM:
    """按顺序返回响应的 mock LLM。"""

    def __init__(self, responses):
        self.responses = list(responses)

    def chat_with_structured_output(self, **_kwargs):
        return self.responses.pop(0)


class V4GraphSmokeTest(unittest.TestCase):
    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        return_value={"result": "ok", "error": None},
    )
    def test_reasoning_acting_loop_ends_with_text(self, _mock):
        llm = _SeqLLM([
            (
                '{"type":"tool_calls","content":{"reason":"r","calls":['
                '{"call_seq":1,"tool_name":"read_file","tool_args":{"file_path":"a.txt"}}]}}'
            ),
            '{"type":"text","content":"任务完成总结"}',
        ])
        graph = build_v4_graph(
            llm_service=llm,
            settings_service=None,
            message_context=None,
            enable_todo=True,
        )
        state = _base_state()
        out = graph.invoke(state)
        self.assertEqual(out.get("final_reply"), "任务完成总结")
        self.assertGreaterEqual(out.get("iteration_count", 0), 1)
        records = out.get("tool_records") or []
        self.assertTrue(any(r.get("call_seq") == 1 for r in records))

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        return_value={"result": None, "error": "失败"},
    )
    def test_failed_tool_then_retry_then_done(self, _mock):
        llm = _SeqLLM([
            (
                '{"type":"tool_calls","content":{"reason":"r","calls":['
                '{"call_seq":1,"tool_name":"sql_query","tool_args":{}}]}}'
            ),
            '{"type":"text","content":"工具失败，已说明原因"}',
        ])
        graph = build_v4_graph(
            llm_service=llm,
            settings_service=None,
            message_context=None,
            enable_todo=True,
        )
        out = graph.invoke(_base_state())
        self.assertEqual(out.get("final_reply"), "工具失败，已说明原因")
        # 失败记录应留在 tool_records 且进入 reasoning 前已产生 acting_failures
        records = out.get("tool_records") or []
        failed = [r for r in records if r.get("call_seq") == 1]
        self.assertEqual(failed[0]["status"], "failed")

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        return_value={"result": "ok", "error": None},
    )
    def test_interrupt_resume_ask_user_question(self, _mock):
        """ask_user_question 触发 interrupt，resume 后继续到 text。"""
        llm = _SeqLLM([
            (
                '{"type":"tool_calls","content":{"reason":"询问用户","calls":['
                '{"call_seq":1,"tool_name":"ask_user_question",'
                '"tool_args":{"question":"是否批准执行？","options":["批准","修改"]}}]}}'
            ),
            '{"type":"text","content":"已按用户意见完成"}',
        ])
        out = run_v4_graph(
            user_message="是否需要批准？",
            workspace_id="ws-interrupt",
            llm_service=llm,
            settings_service=None,
            message_context=None,
            conversation_id="conv-interrupt-test",
        )
        self.assertEqual(out.get("status"), "awaiting_user_input")
        self.assertEqual(out.get("interrupt", {}).get("type"), "ask_user_question")

        out2 = resume_v4_graph("conv-interrupt-test", "批准")
        self.assertEqual(out2.get("final_reply"), "已按用户意见完成")
        records = out2.get("tool_records") or []
        ask = [r for r in records if r.get("tool_name") == "ask_user_question"]
        self.assertEqual(ask[0]["result"], "批准")


class _FakeSettings:
    """最小 settings stub：closuring 启用、预算 8、并行 3。"""

    def __init__(self, **overrides):
        self.values = {
            "agent:closuring_enabled": True,
            "agent:closure_max_rounds": 8,
            "agent:tool_parallelism": 3,
            "agent:ask_user_auto_approve": False,
            "agent:subagent_timeout_seconds": 1800,
        }
        self.values.update(overrides)

    def get(self, key, default=None):
        return self.values.get(key, default)


class ClosuringNodeTest(unittest.TestCase):
    def _node(self, settings=None):
        return create_closuring_node(
            llm_service=None,
            settings_service=settings or _FakeSettings(),
            message_context=None,
        )

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_disabled_routes_finalize(self, _fast_cls):
        node = self._node(settings=_FakeSettings(**{"agent:closuring_enabled": False}))
        out = node(_base_state(pending_final_text="x"))
        self.assertEqual(out["_route_target"], "finalize")

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_passed_routes_finalize(self, fast_cls):
        fast_cls.return_value.chat.return_value = '{"passed": true, "reason": "ok", "feedback": ""}'
        out = self._node()(_base_state(pending_final_text="已完成总结"))
        self.assertEqual(out["_route_target"], "finalize")
        self.assertEqual(out["closure_rounds"], 1)

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_failed_injects_feedback_and_returns_reasoning(self, fast_cls):
        fast_cls.return_value.chat.return_value = (
            '{"passed": false, "reason": "缺少总结", '
            '"feedback": "请先输出 text 总结再 done"}'
        )
        out = self._node()(_base_state(pending_final_text="done"))
        self.assertEqual(out["_route_target"], "reasoning")
        self.assertIn("text 总结", out["closur_feedback"])
        self.assertIsNone(out["pending_final_text"])

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_budget_exceeded_forces_finalize(self, _fast_cls):
        out = self._node()(_base_state(pending_final_text="x", closure_rounds=8))
        self.assertEqual(out["_route_target"], "finalize")
        self.assertEqual(out["closure_rounds"], 9)


class PlanSubagentExecutorTest(unittest.TestCase):
    @patch("service.agent_service.graph.agent_graphs.run_agent_graph")
    @patch("service.agent_service.service.plan_file_service.plan_file_service.create_plan")
    def test_writes_plan_and_returns_content(self, mock_create_plan, mock_run):
        mock_run.return_value = {
            "status": "completed",
            "payload": '{"tasks":[{"description":"步骤1","goal":"g","done_when":"d","phase":"research"}]}',
            "exit_info": {"code": "final_reply"},
        }
        mock_create_plan.return_value = {"success": True}
        result = _execute_call_plan_agent(
            tool_args={
                "task_description": "生成计划",
                "feedback": "加入验证步骤",
            },
            llm_service=object(),
            token_callback=None,
            message_context={
                "workspace_id": "ws-plan",
                "session_id": 42,
                "settings_service": _FakeSettings(),
            },
        )
        self.assertIsNone(result["error"])
        self.assertIn("tasks", result["result"])
        self.assertTrue(result["plan_written"])
        mock_create_plan.assert_called_once()
        # feedback 应拼入任务描述
        call_args = mock_run.call_args[0]
        self.assertIn("加入验证步骤", call_args[1])

    @patch("service.agent_service.graph.agent_graphs.run_agent_graph")
    def test_missing_task_description_returns_error(self, _mock_run):
        result = _execute_call_plan_agent(
            tool_args={},
            llm_service=object(),
            token_callback=None,
            message_context={},
        )
        self.assertIn("缺少 task_description", result["error"])


if __name__ == "__main__":
    unittest.main()
