import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = REPO_ROOT / "deploy" / "e2e" / "control_console.py"
SPEC = importlib.util.spec_from_file_location("control_console", MODULE_PATH)
CONTROL_CONSOLE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROL_CONSOLE
SPEC.loader.exec_module(CONTROL_CONSOLE)


class ControlConsoleTests(unittest.TestCase):
    def test_actions_are_fixed_and_do_not_accept_command_text(self):
        self.assertEqual(
            set(CONTROL_CONSOLE.ACTIONS),
            {
                "compose_up",
                "compose_build",
                "compose_stop",
                "compose_logs",
                "regression",
            },
        )

    def test_compose_command_uses_wsl_and_project_files(self):
        command = CONTROL_CONSOLE.compose_command("ps", "--format", "json")
        self.assertEqual(command[0], "wsl.exe")
        self.assertIn(str(REPO_ROOT), command)
        self.assertIn("compose.yml", command)
        self.assertIn("compose.standalone.yml", command)
        self.assertEqual(command[-3:], ["ps", "--format", "json"])

    @patch.object(CONTROL_CONSOLE, "_run_status_command")
    def test_compose_status_parses_json_lines(self, run_status):
        run_status.return_value = (
            0,
            "\n".join(
                [
                    json.dumps(
                        {
                            "Name": "agentb-agentb-1-1",
                            "Service": "agentb-1",
                            "State": "running",
                            "Health": "healthy",
                            "Status": "Up 1 minute (healthy)",
                        }
                    ),
                    json.dumps(
                        {
                            "Name": "agentb-redis-1",
                            "Service": "redis",
                            "State": "running",
                            "Health": "healthy",
                            "Status": "Up 1 minute (healthy)",
                        }
                    ),
                ]
            ),
        )
        result = CONTROL_CONSOLE.compose_status()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual([item["service"] for item in result["services"]], ["agentb-1", "redis"])

    @patch.object(CONTROL_CONSOLE.subprocess, "run")
    def test_status_timeout_is_reported(self, run):
        run.side_effect = CONTROL_CONSOLE.subprocess.TimeoutExpired("docker", 20)
        exit_code, output = CONTROL_CONSOLE._run_status_command()
        self.assertEqual(exit_code, 124)
        self.assertIn("timed out", output)

    def test_job_manager_rejects_parallel_mutating_jobs(self):
        manager = CONTROL_CONSOLE.JobManager()
        active = CONTROL_CONSOLE.Job(1, "compose_up", status="running")
        manager._jobs[1] = active
        manager._active_job_id = 1
        with self.assertRaisesRegex(RuntimeError, "already running"):
            manager.start("regression")


if __name__ == "__main__":
    unittest.main()
