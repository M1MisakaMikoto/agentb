import re
import unittest
from pathlib import Path

import yaml


TEST_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TEST_ROOT.parents[2]
CONFIG_PATH = TEST_ROOT / "test_config.yaml"
FIXTURE_DOC_PATH = REPO_ROOT / "deploy" / "e2e" / "FIXTURES.md"

EXPECTED_SCENARIOS = [
    "serial_mode",
    "workspace_upload_image_understanding",
    "workspace_upload_read_table_document",
    "qiaozitang_monthly_query",
    "pdf_generate",
    "sql_query",
    "sql_agent_bridge",
    "sql_silent_behavior",
    "sql_permission_fallback",
    "cross_lifecycle",
    "mq_resume",
    "parallel",
    "bridge_defect_extract_parallel",
    "persistent_disease_predict",
    "bridge_predict",
]


class E2ESuiteConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_distributed_suite_contains_only_selected_scenarios(self):
        suites = self.config["suites"]
        self.assertEqual(
            suites["distributed_regression"]["scenarios"],
            EXPECTED_SCENARIOS,
        )
        self.assertEqual(suites["all"]["scenarios"], EXPECTED_SCENARIOS)

    def test_required_fixtures_use_single_local_root(self):
        required = {
            path
            for scenario in self.config.get("scenarios", {}).values()
            for path in scenario.get("required_files", [])
        }
        self.assertEqual(len(required), 16)
        self.assertTrue(
            all(path.startswith(".dev/fixture/") for path in required),
            required,
        )

    def test_configured_source_files_are_preflighted(self):
        for scenario_name, scenario in self.config.get("scenarios", {}).items():
            configured_sources = set(scenario.get("source_files", []))
            source_file = scenario.get("source_file")
            if source_file:
                configured_sources.add(source_file)
            if not configured_sources:
                continue

            required = set(scenario.get("required_files", []))
            self.assertEqual(
                configured_sources,
                required,
                f"{scenario_name} source files must match fixture preflight paths",
            )

    def test_fixture_document_matches_configuration(self):
        required = {
            path
            for scenario in self.config.get("scenarios", {}).values()
            for path in scenario.get("required_files", [])
        }
        documented = set(
            re.findall(
                r"`(\.dev/fixture/[^`]+)`",
                FIXTURE_DOC_PATH.read_text(encoding="utf-8"),
            )
        )
        self.assertEqual(documented, required)


if __name__ == "__main__":
    unittest.main()
