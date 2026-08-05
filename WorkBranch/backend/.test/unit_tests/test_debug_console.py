import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = REPO_ROOT / "deploy" / "e2e" / "debug_console.py"
SPEC = importlib.util.spec_from_file_location("debug_console", MODULE_PATH)
DEBUG_CONSOLE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = DEBUG_CONSOLE
SPEC.loader.exec_module(DEBUG_CONSOLE)


class DebugConsoleTests(unittest.TestCase):
    def test_actions_are_fixed_argument_vectors(self):
        command = DEBUG_CONSOLE.action_command("start-deps")
        self.assertEqual(command[:2], ["docker", "compose"])
        self.assertEqual(command[-4:], ["up", "-d", "mysql", "redis"])

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(KeyError):
            DEBUG_CONSOLE.action_command("shell?command=whoami")

    def test_status_parser_accepts_compose_json_lines(self):
        sample = '{"Service":"redis","State":"running","Health":"healthy"}\n'

        class Completed:
            returncode = 0
            stdout = sample
            stderr = ""

        original_run = DEBUG_CONSOLE.subprocess.run
        DEBUG_CONSOLE.subprocess.run = lambda *args, **kwargs: Completed()
        try:
            status = DEBUG_CONSOLE.compose_status()
        finally:
            DEBUG_CONSOLE.subprocess.run = original_run

        self.assertTrue(status["ok"])
        self.assertEqual(status["services"][0]["Service"], "redis")


if __name__ == "__main__":
    unittest.main()
