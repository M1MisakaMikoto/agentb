import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.decision.tool_call_parser import (
    DecisionParseError,
    parse_leader_output,
)
from service.agent_service.graph.v4.protocol import (
    leader_output_json_schema,
    parse_leader_output_dict,
    validate_leader_output,
)


class ParseLeaderOutputTest(unittest.TestCase):
    def test_tool_calls_with_reason(self):
        raw = (
            '{"type":"tool_calls","content":{"reason":"并行读取","calls":['
            '{"call_seq":1,"tool_name":"read_file","tool_args":{"file_path":"a.txt"},'
            '"task_description":"读取"},'
            '{"call_seq":2,"tool_name":"search_files","tool_args":{"pattern":"*.md"}}]}}'
        )
        data = parse_leader_output(raw)
        self.assertEqual(data["type"], "tool_calls")
        self.assertEqual(data["content"]["reason"], "并行读取")
        self.assertEqual(len(data["content"]["calls"]), 2)
        self.assertEqual(data["content"]["calls"][1]["call_seq"], 2)

    def test_text(self):
        data = parse_leader_output('{"type":"text","content":"最终总结"}')
        self.assertEqual(data["type"], "text")
        self.assertEqual(data["content"], "最终总结")

    def test_done(self):
        data = parse_leader_output('{"type":"done","content":null}')
        self.assertEqual(data["type"], "done")
        self.assertIsNone(data["content"])

    def test_fenced(self):
        raw = '```json\n{"type":"text","content":"ok"}\n```'
        data = parse_leader_output(raw)
        self.assertEqual(data["content"], "ok")

    def test_rejects_unknown_type(self):
        with self.assertRaises(DecisionParseError) as ctx:
            parse_leader_output('{"type":"chat","content":"x"}')
        self.assertEqual(ctx.exception.category, "schema")

    def test_rejects_missing_content(self):
        with self.assertRaises(DecisionParseError):
            parse_leader_output('{"type":"tool_calls"}')

    def test_rejects_empty_calls(self):
        with self.assertRaises(DecisionParseError):
            parse_leader_output(
                '{"type":"tool_calls","content":{"reason":"r","calls":[]}}'
            )

    def test_rejects_garbage(self):
        with self.assertRaises(DecisionParseError) as ctx:
            parse_leader_output("完全不是 JSON")
        self.assertEqual(ctx.exception.category, "json_syntax")


class ValidateLeaderOutputTest(unittest.TestCase):
    def test_valid_tool_calls(self):
        data = parse_leader_output_dict(
            {
                "type": "tool_calls",
                "content": {
                    "reason": "r",
                    "calls": [
                        {"call_seq": 1, "tool_name": "read_file", "tool_args": {}}
                    ],
                },
            }
        )
        self.assertEqual(validate_leader_output(data, {"read_file"}), [])

    def test_duplicate_call_seq(self):
        data = parse_leader_output_dict(
            {
                "type": "tool_calls",
                "content": {
                    "reason": "r",
                    "calls": [
                        {"call_seq": 1, "tool_name": "read_file"},
                        {"call_seq": 1, "tool_name": "write_file"},
                    ],
                },
            }
        )
        issues = validate_leader_output(data, {"read_file", "write_file"})
        self.assertTrue(any("call_seq 重复" in i for i in issues))

    def test_unknown_tool(self):
        data = parse_leader_output_dict(
            {
                "type": "tool_calls",
                "content": {
                    "reason": "r",
                    "calls": [{"call_seq": 1, "tool_name": "nope"}],
                },
            }
        )
        issues = validate_leader_output(data, {"read_file"})
        self.assertTrue(any("不在协议内" in i for i in issues))

    def test_schema_shape(self):
        schema = leader_output_json_schema()
        self.assertEqual(schema["name"], "leader_output")
        self.assertIn("tool_calls", schema["schema"]["properties"]["type"]["enum"])
        content_schema = schema["schema"]["properties"]["content"]
        self.assertNotIn("oneOf", content_schema)
        self.assertEqual(
            [branch.get("type") for branch in content_schema["anyOf"]],
            ["object", "string", "null"],
        )


if __name__ == "__main__":
    unittest.main()
