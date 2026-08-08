import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools  # noqa: E402
from service.agent_service.tools.registry import ALL_TOOLS  # noqa: E402


class RetiredToolCleanupTests(unittest.TestCase):
    class _Settings:
        def __init__(self, version):
            self.version = version

        def get(self, key, default=None):
            if key == "agent:orchestration_version":
                return self.version
            return default

    def test_director_excludes_retired_chat(self):
        self.assertNotIn(
            "chat",
            get_allowed_tools("director_agent", self._Settings("v4")),
        )

    def test_legacy_v3_keeps_chat_termination_tool(self):
        self.assertIn(
            "chat",
            get_allowed_tools("director_agent", self._Settings("v3")),
        )

    def test_review_excludes_unregistered_glob_grep(self):
        tools = get_allowed_tools("review_agent", None)
        self.assertNotIn("glob", tools)
        self.assertNotIn("grep", tools)

    def test_plan_agent_excludes_switch_execution_mode(self):
        self.assertNotIn(
            "switch_execution_mode",
            get_allowed_tools("plan_agent", None),
        )

    def test_main_registry_excludes_retired_tools(self):
        self.assertNotIn("switch_execution_mode", ALL_TOOLS)
        self.assertNotIn("enter_plan_mode", ALL_TOOLS)


if __name__ == "__main__":
    unittest.main()
