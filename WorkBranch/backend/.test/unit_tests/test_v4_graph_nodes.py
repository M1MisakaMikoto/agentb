import json
import os
import re
import sys
import unittest
from unittest.mock import patch


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.v4.acting import create_acting_node
from service.agent_service.graph.director_agent import create_analyze_node
from service.agent_service.graph.agent_graphs import create_agent_graph
from service.agent_service.graph.v4.closuring import (
    CLOSURING_PROMPT,
    _build_feedback_check_prompt,
    create_closuring_node,
)
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
from service.agent_service.tools.document_tools import (
    _build_pandoc_read_result,
    execute_document,
)


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

    def test_malformed_outer_json_with_nested_text_retries_inside_reasoning(self):
        raw = (
            '{"type":"text","content": broken '
            '{"type":"text","content":"不能截取为完成结果"}'
        )
        result = self._node(raw)(_base_state())
        self.assertEqual(result["_route_target"], "reasoning")
        self.assertEqual(result["decision_error_count"], 1)
        self.assertIn("json_syntax", result["parse_error"])
        self.assertNotIn("pending_final_text", result)

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
    def _node(self, token_callback=None):
        return create_acting_node(
            llm_service=None,
            token_callback=token_callback,
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

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        return_value={"result": "ok", "error": None},
    )
    def test_forwards_token_callback_to_tool_execution(self, mock_run):
        callback = object()
        state = _base_state(
            pending_batch={
                "reason": "r",
                "calls": [{"call_seq": 1, "tool_name": "read_file", "tool_args": {}}],
            }
        )

        self._node(token_callback=callback)(state)

        self.assertIs(mock_run.call_args.kwargs["token_callback"], callback)

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        return_value={"result": "ok", "error": None},
    )
    def test_subagent_uses_call_level_task_description(self, mock_run):
        state = _base_state(
            pending_batch={
                "reason": "r",
                "calls": [{
                    "call_seq": 1,
                    "tool_name": "call_plan_agent",
                    "tool_args": {},
                    "task_description": "生成登录计划",
                }],
            }
        )

        self._node()(state)

        self.assertEqual(
            mock_run.call_args.kwargs["tool_args"]["task_description"],
            "生成登录计划",
        )


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


class AnalyzeNodeTest(unittest.TestCase):
    def test_string_execution_mode_is_emitted_without_enum_error(self):
        events = []
        node = create_analyze_node(
            message_context={
                "send_message": lambda content, segment_type, metadata: events.append(
                    (content, segment_type, metadata)
                )
            }
        )

        result = node(_base_state(execution_mode="DIRECT"))

        self.assertEqual(result["execution_mode"], "DIRECT")
        self.assertEqual(events[0][2]["execution_mode"], "DIRECT")

    @patch(
        "service.agent_service.graph.agent_graphs.create_child_agent_graph",
        return_value="child-graph",
    )
    def test_plan_agent_uses_child_graph(self, mock_create_child):
        graph = create_agent_graph("plan_agent")

        self.assertEqual(graph, "child-graph")
        mock_create_child.assert_called_once()

class PromptTagTest(unittest.TestCase):
    @patch(
        "service.agent_service.tools.document_tools.os.path.getsize",
        return_value=12345,
    )
    def test_pandoc_result_has_hermes_pagination_metadata(self, _mock_size):
        full_text = "标题\n\n测点|值\nCGY-QX01-1|12.91\n尾行"
        result = _build_pandoc_read_result(
            "report.docx",
            full_text,
            start_idx=0,
            max_length=len("标题\n\n测点|值\nCGY-QX01-1|12.91\n"),
            include_metadata=True,
        )

        self.assertEqual(result["total_lines"], 5)
        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 4)
        self.assertEqual(result["file_size"], 12345)
        self.assertEqual(result["next_start_idx"], len(result["content"]))
        self.assertTrue(result["truncated"])

    @patch(
        "service.agent_service.tools.document_tools.os.path.getsize",
        return_value=12345,
    )
    def test_character_page_stops_at_complete_line(self, _mock_size):
        result = _build_pandoc_read_result(
            "report.docx",
            "alpha\nbeta-long\nomega",
            start_idx=0,
            max_length=10,
            include_metadata=True,
        )

        self.assertEqual(result["content"], "alpha\n")
        self.assertEqual(result["read_range"], "0-6")
        self.assertEqual(result["next_start_idx"], 6)
        self.assertEqual(result["end_line"], 1)

    @patch(
        "service.agent_service.tools.document_tools.os.path.getsize",
        return_value=12345,
    )
    def test_pandoc_result_supports_hermes_line_pagination(self, _mock_size):
        result = _build_pandoc_read_result(
            "report.docx",
            "one\ntwo\nthree\nfour\n",
            start_idx=0,
            max_length=100000,
            include_metadata=True,
            offset=2,
            limit=2,
        )

        self.assertEqual(result["content"], "two\nthree\n")
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 3)
        self.assertEqual(result["next_offset"], 4)
        self.assertEqual(result["pagination_mode"], "lines")
        self.assertTrue(result["truncated"])

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
        # meta（含轮次数字）必须在 user 末尾，避免破坏每轮前缀缓存命中
        self.assertTrue(user_message.startswith("<system>"))
        self.assertIn("轮次: 1/10", user_message)
        self.assertTrue(user_message.rstrip().endswith("agent_type: director_agent"))

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
        self.assertIn('reason="第二批"', text)

    def test_tool_records_include_complete_call_information(self):
        text = format_tool_records([
            {"round": 1, "reason": "读取目标"},
            {"round": 1, "call_seq": 1, "tool_name": "read_file",
             "status": "success", "args": {"file_path": "目标.txt"},
             "task_description": "读取目标文件", "result": "内容"},
        ])
        self.assertIn('request={"file_path": "目标.txt"}', text)
        self.assertIn('task_description="读取目标文件"', text)
        self.assertIn("result=内容", text)

    def test_tool_records_do_not_clip_regular_tool_result(self):
        long_result = "数" * 5000
        text = format_tool_records([
            {"round": 1, "reason": "r"},
            {"round": 1, "call_seq": 1, "tool_name": "document",
             "status": "success", "result": long_result},
        ])
        self.assertIn(long_result, text)
        self.assertNotIn("中间省略", text)

    def test_pandoc_document_read_uses_hermes_style_result(self):
        raw_content = "标题\n\n测点|值\nCGY-QX01-1|12.91\n"
        text = format_tool_records([
            {"round": 1, "reason": "读取报告"},
            {
                "round": 1,
                "call_seq": 1,
                "tool_name": "document",
                "status": "success",
                "args": {
                    "operation": "r",
                    "file_path": "report.docx",
                    "start_idx": 0,
                    "max_length": len(raw_content),
                },
                "result": {
                    "content": raw_content,
                    "metadata": {"file_type": "docx", "method": "pandoc"},
                    "structure": [],
                    "total_length": 100,
                    "total_lines": 10,
                    "start_line": 1,
                    "end_line": 4,
                    "file_size": 12345,
                    "read_range": f"0-{len(raw_content)}",
                    "next_start_idx": len(raw_content),
                    "truncated": True,
                },
            },
        ])

        payload = json.loads(text.split(" result=", 1)[1])
        self.assertEqual(
            set(payload),
            {
                "content",
                "total_lines",
                "file_size",
                "truncated",
                "extracted_document",
                "hint",
                "read_range",
                "next_start_idx",
            },
        )
        self.assertEqual(
            payload["content"],
            "1|标题\n2|\n3|测点|值\n4|CGY-QX01-1|12.91",
        )
        recovered = "\n".join(
            line.split("|", 1)[1] for line in payload["content"].splitlines()
        )
        self.assertEqual(recovered, raw_content.rstrip("\n"))
        self.assertEqual(payload["total_lines"], 10)
        self.assertEqual(payload["file_size"], 12345)
        self.assertTrue(payload["truncated"])
        self.assertTrue(payload["extracted_document"])
        self.assertEqual(payload["next_start_idx"], len(raw_content))
        self.assertIn(f"start_idx={len(raw_content)}", payload["hint"])

    def test_line_paginated_docx_uses_exact_hermes_continuation_style(self):
        text = format_tool_records([
            {
                "round": 1,
                "call_seq": 1,
                "tool_name": "document",
                "status": "success",
                "args": {
                    "operation": "r",
                    "file_path": "report.docx",
                    "offset": 2,
                    "limit": 2,
                },
                "result": {
                    "content": "two\nthree\n",
                    "metadata": {"file_type": "docx", "method": "pandoc"},
                    "structure": [],
                    "total_length": 19,
                    "total_lines": 4,
                    "start_line": 2,
                    "end_line": 3,
                    "file_size": 12345,
                    "read_range": "4-14",
                    "pagination_mode": "lines",
                    "next_offset": 4,
                    "truncated": True,
                },
            },
        ])

        payload = json.loads(text.split(" result=", 1)[1])
        self.assertEqual(
            set(payload),
            {
                "content",
                "total_lines",
                "file_size",
                "truncated",
                "extracted_document",
                "hint",
            },
        )
        self.assertEqual(payload["content"], "2|two\n3|three")
        self.assertEqual(
            payload["hint"],
            "Use offset=4 to continue reading (showing 2-3 of 4 lines)",
        )

    def test_python_docx_read_uses_hermes_style_result(self):
        text = format_tool_records([
            {
                "round": 1,
                "call_seq": 1,
                "tool_name": "document",
                "status": "success",
                "args": {
                    "operation": "r",
                    "file_path": "report.docx",
                    "start_idx": 0,
                    "max_length": 5,
                },
                "result": {
                    "content": "alpha",
                    "metadata": {"file_type": "docx", "method": "python-docx"},
                    "structure": [],
                    "total_length": 10,
                    "total_lines": 2,
                    "start_line": 1,
                    "end_line": 1,
                    "file_size": 12345,
                    "read_range": "0-5",
                    "next_start_idx": 5,
                    "truncated": True,
                },
            },
        ])

        payload = json.loads(text.split(" result=", 1)[1])
        self.assertEqual(payload["content"], "1|alpha")
        self.assertTrue(payload["extracted_document"])

    @patch("service.agent_service.tools.document_tools.get_settings_service")
    @patch("service.agent_service.tools.document_tools._docx_read")
    @patch("service.agent_service.tools.document_tools.os.path.isfile", return_value=True)
    @patch("service.agent_service.tools.document_tools.os.path.exists", return_value=True)
    def test_execute_document_passes_line_pagination(
        self,
        _mock_exists,
        _mock_isfile,
        mock_docx_read,
        mock_settings,
    ):
        mock_settings.return_value.get.return_value = True
        mock_docx_read.return_value = {"result": {"content": "x"}, "error": None}

        execute_document({
            "operation": "r",
            "file_path": "report.docx",
            "offset": 5,
            "limit": 20,
        })

        mock_docx_read.assert_called_once_with(
            "report.docx",
            0,
            100000,
            True,
            None,
            offset=5,
            limit=20,
        )

    @patch("service.agent_service.tools.document_tools.get_settings_service")
    @patch("service.agent_service.tools.document_tools._docx_read")
    @patch("service.agent_service.tools.document_tools.os.path.isfile", return_value=True)
    @patch("service.agent_service.tools.document_tools.os.path.exists", return_value=True)
    def test_execute_document_fills_missing_line_pagination_default(
        self,
        _mock_exists,
        _mock_isfile,
        mock_docx_read,
        mock_settings,
    ):
        mock_settings.return_value.get.return_value = True
        mock_docx_read.return_value = {"result": {"content": "x"}, "error": None}

        execute_document({
            "operation": "r",
            "file_path": "report.docx",
            "offset": 5,
        })

        mock_docx_read.assert_called_once_with(
            "report.docx",
            0,
            100000,
            True,
            None,
            offset=5,
            limit=2000,
        )

    @patch("service.agent_service.tools.document_tools.get_settings_service")
    @patch("service.agent_service.tools.document_tools._docx_read")
    @patch("service.agent_service.tools.document_tools.os.path.isfile", return_value=True)
    @patch("service.agent_service.tools.document_tools.os.path.exists", return_value=True)
    def test_execute_document_rejects_line_limit_above_hermes_maximum(
        self,
        _mock_exists,
        _mock_isfile,
        mock_docx_read,
        mock_settings,
    ):
        mock_settings.return_value.get.return_value = True

        result = execute_document({
            "operation": "r",
            "file_path": "report.docx",
            "limit": 2001,
        })

        self.assertEqual(result["error"], "limit must be between 1 and 2000")
        mock_docx_read.assert_not_called()

    def test_non_pandoc_document_result_keeps_existing_format(self):
        result = {"content": "PDF", "metadata": {"file_type": "pdf"}}
        text = format_tool_records([
            {
                "round": 1,
                "call_seq": 1,
                "tool_name": "document",
                "status": "success",
                "args": {"operation": "r", "file_path": "report.pdf"},
                "result": result,
            },
        ])
        self.assertIn(f"result={result}", text)

    def test_tool_records_clip_subagent_return_only(self):
        long_result = "数" * 5000
        text = format_tool_records([
            {"round": 1, "reason": "r"},
            {"round": 1, "call_seq": 1, "tool_name": "call_prediction_agent",
             "status": "success", "result": long_result},
        ])
        self.assertNotIn(long_result, text)
        self.assertIn("中间省略", text)


class _SeqLLM:
    """按顺序返回响应的 mock LLM。"""

    def __init__(self, responses):
        self.responses = list(responses)

    def chat_with_structured_output(self, **_kwargs):
        return self.responses.pop(0)


class _CapturingLLM(_SeqLLM):
    def chat_with_structured_output(self, **kwargs):
        self.last_kwargs = kwargs
        return super().chat_with_structured_output(**kwargs)


class V4GraphSmokeTest(unittest.TestCase):
    def test_director_sends_only_xml_embedded_system_prompt(self):
        llm = _CapturingLLM(['{"type":"text","content":"完成"}'])
        node = create_reasoning_node(llm_service=llm)

        out = node(_base_state())

        self.assertEqual(out["_route_target"], "finalize")
        self.assertIsNone(llm.last_kwargs["system_prompt"])
        message = llm.last_kwargs["messages"][0]
        self.assertEqual(message["role"], "user")
        self.assertTrue(message["content"].startswith("<system>\n"))
        self.assertEqual(len(re.findall(r"(?m)^<system>$", message["content"])), 1)

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        return_value={"result": "ok", "error": None},
    )
    def test_reasoning_acting_loop_ends_with_text(self, _mock):
        llm = _CapturingLLM([
            (
                '{"type":"tool_calls","content":{"reason":"r","calls":['
                '{"call_seq":1,"tool_name":"read_file","tool_args":{"file_path":"a.txt"},'
                '"task_description":"读取文件"}]}}'
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
        call = next(r for r in records if r.get("call_seq") == 1)
        self.assertEqual(call.get("round"), 1)
        self.assertEqual(call.get("args"), {"file_path": "a.txt"})
        self.assertEqual(call.get("task_description"), "读取文件")
        self.assertEqual(out.get("current_conversation_messages"), [])
        self.assertNotIn("\n\n<context>\n", llm.last_kwargs["messages"][0]["content"])

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

    @patch(
        "service.agent_service.graph.subgraphs.tool_executor.run_tool_execution",
        return_value={"result": "ok", "error": None},
    )
    def test_resume_rejects_mismatched_call_seq(self, _mock):
        llm = _SeqLLM([
            (
                '{"type":"tool_calls","content":{"reason":"询问用户","calls":['
                '{"call_seq":1,"tool_name":"ask_user_question",'
                '"tool_args":{"question":"是否批准？"}}]}}'
            ),
            '{"type":"text","content":"已按用户意见完成"}',
        ])
        out = run_v4_graph(
            user_message="需要批准",
            workspace_id="ws-interrupt-seq",
            llm_service=llm,
            settings_service=None,
            message_context=None,
            conversation_id="conv-interrupt-seq",
        )
        self.assertEqual(out.get("status"), "awaiting_user_input")

        with self.assertRaisesRegex(ValueError, "call_seq 不匹配"):
            resume_v4_graph("conv-interrupt-seq", "批准", expected_call_seq=2)

        out2 = resume_v4_graph("conv-interrupt-seq", "批准", expected_call_seq=1)
        self.assertEqual(out2.get("final_reply"), "已按用户意见完成")


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
    def test_business_quality_rejection_cannot_return_to_reasoning(self, fast_cls):
        fast_cls.return_value.chat.return_value = (
            '{"passed": false, '
            '"reason": "数据不完整，无法准确分析变化规律及预测值", '
            '"feedback": "补充读取业务数据后重写报告"}'
        )

        out = self._node()(_base_state(pending_final_text="已向用户回复"))

        self.assertEqual(out["_route_target"], "finalize")
        self.assertEqual(out["closure_rounds"], 1)

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_missing_final_reply_returns_to_reasoning_without_llm(self, fast_cls):
        out = self._node()(_base_state(
            pending_final_text=None,
            final_reply=None,
        ))

        self.assertEqual(out["_route_target"], "reasoning")
        self.assertIn("最终回复", out["closur_feedback"])
        fast_cls.assert_not_called()

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_false_missing_reply_claim_cannot_return_to_reasoning(self, fast_cls):
        fast_cls.return_value.chat.return_value = (
            '{"passed": false, "failure_kind": "missing_final_reply", '
            '"reason": "没有最终回复", "feedback": "请回复用户"}'
        )

        out = self._node()(_base_state(pending_final_text="实际已经回复"))

        self.assertEqual(out["_route_target"], "finalize")

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_missing_required_artifact_returns_to_reasoning(self, fast_cls):
        fast_cls.return_value.chat.return_value = (
            '{"passed": false, '
            '"failure_kind": "required_artifact_not_generated", '
            '"reason": "用户要求的 PDF 未生成", '
            '"feedback": "请生成 PDF 文件"}'
        )
        out = self._node()(_base_state(
            current_user_message_text="请生成 PDF 报告",
            pending_final_text="已回复",
        ))
        self.assertEqual(out["_route_target"], "reasoning")
        self.assertIn("PDF", out["closur_feedback"])
        self.assertIsNone(out["pending_final_text"])

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_missing_required_external_action_returns_to_reasoning(self, fast_cls):
        fast_cls.return_value.chat.return_value = (
            '{"passed": false, '
            '"failure_kind": "required_external_action_not_executed", '
            '"reason": "用户要求的提交动作未执行", '
            '"feedback": "请执行提交动作"}'
        )

        out = self._node()(_base_state(
            current_user_message_text="请将报告提交到平台",
            pending_final_text="已回复",
        ))

        self.assertEqual(out["_route_target"], "reasoning")
        self.assertIn("提交动作", out["closur_feedback"])

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_budget_exceeded_forces_finalize(self, _fast_cls):
        out = self._node()(_base_state(pending_final_text="x", closure_rounds=8))
        self.assertEqual(out["_route_target"], "finalize")
        self.assertEqual(out["closure_rounds"], 9)

    @patch("service.agent_service.service.llm_service.FastLLMService")
    def test_judgment_prompt_contains_behavior_facts_without_business_content(self, fast_cls):
        fast_cls.return_value.chat.return_value = (
            '{"passed": true, "reason": "leader 已输出总结", "feedback": ""}'
        )
        state = _base_state(
            current_user_message_text="USER-QUESTION-9271",
            pending_final_text="FINAL-TEXT-9271",
            tool_records=[{
                "round": 1,
                "call_seq": 1,
                "tool_name": "read_file",
                "status": "success",
                "args": {"path": "TOOL-REQUEST-9271"},
                "result": "TOOL-RESULT-9271",
            }],
            parent_chain_messages=[
                {"role": "user", "content": "PARENT-CONTEXT-9271"},
            ],
            current_conversation_messages=[
                {"role": "assistant", "content": "CURRENT-CONTEXT-9271"},
            ],
        )

        out = self._node()(state)

        self.assertEqual(out["_route_target"], "finalize")
        judgment_prompt = fast_cls.return_value.chat.call_args[1]["messages"][0]["content"]
        self.assertIn("USER-QUESTION-9271", judgment_prompt)
        self.assertIn('"final_reply_present": true', judgment_prompt)
        self.assertNotIn("FINAL-TEXT-9271", judgment_prompt)
        self.assertIn("TOOL-REQUEST-9271", judgment_prompt)
        self.assertNotIn("TOOL-RESULT-9271", judgment_prompt)
        self.assertNotIn("PARENT-CONTEXT-9271", judgment_prompt)
        self.assertNotIn("CURRENT-CONTEXT-9271", judgment_prompt)

    def test_prompt_limits_closure_to_programmatic_checks(self):
        self.assertIn("只审查与业务内容无关、可客观验证的 Agent 行为", CLOSURING_PROMPT)
        self.assertIn("type=text 最终回复", CLOSURING_PROMPT)
        self.assertIn("文件类型已经生成", CLOSURING_PROMPT)
        self.assertIn("工具描述能证明目标路径不可达", CLOSURING_PROMPT)
        self.assertIn("不读取或评价最终回复正文", CLOSURING_PROMPT)
        self.assertIn("如果只有业务问题，必须通过", CLOSURING_PROMPT)
        self.assertIn("failure_kind", CLOSURING_PROMPT)
        self.assertNotIn("已完成的可行部分", CLOSURING_PROMPT)
        self.assertIn("禁止审查事实准确性", CLOSURING_PROMPT)
        self.assertIn("业务内容完整性", CLOSURING_PROMPT)
        self.assertIn("预测合理性", CLOSURING_PROMPT)
        self.assertIn("证据充分度", CLOSURING_PROMPT)

    def test_judgment_prompt_includes_director_visible_tool_descriptions(self):
        prompt = _build_feedback_check_prompt(_base_state(), _FakeSettings())

        self.assertIn("Director 可见的工具描述", prompt)
        self.assertIn("document:", prompt)
        self.assertNotIn("thinking:", prompt)
        self.assertNotIn("call_review_agent:", prompt)


    @patch("service.agent_service.graph.v4.closuring.console.warning")
    def test_tool_history_clips_request_and_excludes_result_content(self, warning):
        prompt = _build_feedback_check_prompt(_base_state(tool_records=[{
            "round": 1,
            "call_seq": 7,
            "tool_name": "document",
            "status": "success",
            "args": {"file_path": "q" * 120 + ".pdf"},
            "result": "r" * 130,
        }]))

        self.assertIn("q" * 80, prompt)
        self.assertNotIn("r" * 80, prompt)
        self.assertEqual(warning.call_count, 1)
        warnings = "\\n".join(call.args[0] for call in warning.call_args_list)
        self.assertIn("field=request", warnings)
        self.assertIn("call_seq=7", warnings)

    @patch("service.agent_service.graph.v4.closuring.console.warning")
    def test_tool_history_short_fields_are_not_warned(self, warning):
        prompt = _build_feedback_check_prompt(_base_state(tool_records=[{
            "round": 1,
            "call_seq": 2,
            "tool_name": "read_file",
            "status": "failed",
            "args": {"path": "short.txt"},
            "error": "not found",
        }]))

        self.assertIn("short.txt", prompt)
        self.assertNotIn("not found", prompt)
        warning.assert_not_called()

    def test_tool_history_excludes_business_payload_arguments(self):
        prompt = _build_feedback_check_prompt(_base_state(tool_records=[{
            "round": 1,
            "call_seq": 3,
            "tool_name": "document",
            "status": "success",
            "args": {
                "operation": "w",
                "file_path": "report.pdf",
                "content": "BUSINESS-CONTENT-4731",
                "query": "BUSINESS-QUERY-4731",
            },
            "result": "BUSINESS-RESULT-4731",
        }]))

        self.assertIn("report.pdf", prompt)
        self.assertIn('\\\"operation\\\": \\\"w\\\"', prompt)
        self.assertNotIn("BUSINESS-CONTENT-4731", prompt)
        self.assertNotIn("BUSINESS-QUERY-4731", prompt)
        self.assertNotIn("BUSINESS-RESULT-4731", prompt)


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
    @patch(
        "service.agent_service.service.plan_file_service.plan_file_service.create_plan",
        side_effect=RuntimeError("disk full"),
    )
    def test_plan_write_failure_is_tool_error(self, _mock_create, mock_run):
        mock_run.return_value = {
            "status": "completed",
            "payload": '{"tasks":[]}',
            "exit_info": {"code": "final_reply"},
        }

        result = _execute_call_plan_agent(
            tool_args={"task_description": "生成计划"},
            llm_service=object(),
            message_context={
                "workspace_id": "ws-plan",
                "session_id": 42,
                "settings_service": _FakeSettings(),
            },
        )

        self.assertIn("计划文件写入失败", result["error"])
        self.assertFalse(result["plan_written"])

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
