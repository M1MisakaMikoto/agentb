import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.decision.tool_call_parser import (
    DecisionParseError,
    extract_json_object,
    parse_intent_response,
    parse_tool_decision_response,
)


class ExtractJsonObjectTest(unittest.TestCase):
    def test_direct_json(self):
        self.assertEqual(
            extract_json_object('{"kind":"tool","tool_name":"read_file"}'),
            {"kind": "tool", "tool_name": "read_file"},
        )

    def test_json_fence(self):
        raw = '以下是分析结果：\n```json\n{"kind":"step_done","reply":"ok"}\n```\n以上是结果'
        self.assertEqual(
            extract_json_object(raw),
            {"kind": "step_done", "reply": "ok"},
        )

    def test_balanced_extraction_with_surrounding_text(self):
        raw = 'ok, decision: {"kind":"tool","tool_name":"read_file","tool_args":{"file_path":"a.txt"}} done'
        data = extract_json_object(raw)
        self.assertEqual(data["kind"], "tool")
        self.assertEqual(data["tool_name"], "read_file")

    def test_array_extraction(self):
        raw = '[{"kind":"step_done","reply":"ok"}]'
        data = extract_json_object(raw)
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["kind"], "step_done")

    def test_trailing_comma_repair(self):
        raw = '{"kind":"tool","tool_name":"read_file","tool_args":{"file_path":"a.txt",},}'
        data = extract_json_object(raw)
        self.assertEqual(data["kind"], "tool")

    def test_truncation_repair(self):
        raw = '{"kind":"tool","tool_name":"read_file","tool_args":{"file_path":"a.txt"}'
        data = extract_json_object(raw)
        self.assertEqual(data["tool_name"], "read_file")
        self.assertEqual(data["tool_args"]["file_path"], "a.txt")

    def test_empty_raises(self):
        with self.assertRaises(DecisionParseError) as ctx:
            extract_json_object("")
        self.assertEqual(ctx.exception.category, "json_syntax")

    def test_garbage_raises(self):
        with self.assertRaises(DecisionParseError) as ctx:
            extract_json_object("这根本不是 JSON")
        self.assertEqual(ctx.exception.category, "json_syntax")


class ParseToolDecisionResponseTest(unittest.TestCase):
    def test_accepts_fenced_tool_decision(self):
        data = parse_tool_decision_response(
            '```json\n{"kind":"tool","tool_name":"read_file","tool_args":{"file_path":"a"}}\n```'
        )
        self.assertEqual(data["kind"], "tool")
        self.assertEqual(data["tool_name"], "read_file")

    def test_tolerates_array_of_one(self):
        data = parse_tool_decision_response('[{"kind":"step_done","reply":"完成"}]')
        self.assertEqual(data["kind"], "step_done")

    def test_rejects_schema_violation(self):
        with self.assertRaises(DecisionParseError) as ctx:
            parse_tool_decision_response('{"kind":"unknown"}')
        self.assertEqual(ctx.exception.category, "schema")

    def test_rejects_top_level_scalar(self):
        with self.assertRaises(DecisionParseError) as ctx:
            parse_tool_decision_response('"step_done"')
        self.assertEqual(ctx.exception.category, "not_object")

    def test_rejects_empty_array(self):
        with self.assertRaises(DecisionParseError) as ctx:
            parse_tool_decision_response("[]")
        self.assertEqual(ctx.exception.category, "json_syntax")


class ParseIntentResponseTest(unittest.TestCase):
    def test_valid(self):
        result = parse_intent_response(
            '{"is_malicious":true,"rewritten_query":"查天气"}',
            "原始消息",
        )
        self.assertTrue(result["is_malicious"])
        self.assertEqual(result["rewritten_query"], "查天气")

    def test_fallback_on_malformed(self):
        self.assertIsNone(parse_intent_response("不是 JSON", "原始消息"))

    def test_fallback_on_non_dict(self):
        self.assertIsNone(parse_intent_response("[1,2,3]", "原始消息"))


if __name__ == "__main__":
    unittest.main()
