import os
import sys
import unittest
from unittest.mock import patch


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.service.llm_service import LLMService


class _Settings:
    _values = {
        "llm:structured_output": "auto",
        "llm:model": "deepseek-v4-flash",
    }

    def get(self, key):
        return self._values[key]


class _RejectingLLM:
    def bind(self, **_kwargs):
        return self

    def invoke(self, _messages):
        raise RuntimeError("invalid response_format json_schema")


class StructuredOutputFallbackTest(unittest.TestCase):
    def setUp(self):
        LLMService._instance = None
        LLMService._json_schema_unavailable = False

    def tearDown(self):
        LLMService._instance = None
        LLMService._json_schema_unavailable = False

    def test_schema_failure_logs_errors_before_fallback(self):
        service = LLMService(_Settings())
        service._llm = _RejectingLLM()
        events = []
        service._log_llm_event = (
            lambda level, event, msg, **kwargs: events.append((level, event))
        )
        service._build_lc_messages = lambda *args, **kwargs: []
        service.chat_with_json_mode = (
            lambda *args, **kwargs: '{"type":"done","content":null}'
        )

        def ascii_console(*values, **_kwargs):
            " ".join(str(value) for value in values).encode("ascii")

        with patch("builtins.print", side_effect=ascii_console):
            result = service.chat_with_structured_output(
                messages=[],
                schema={"name": "leader_output", "schema": {"type": "object"}},
            )

        self.assertEqual(result, '{"type":"done","content":null}')
        self.assertEqual(
            events,
            [
                ("INFO", "llm.call.started"),
                ("ERROR", "llm.call.failed"),
                ("ERROR", "llm.structured_output_fallback"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
