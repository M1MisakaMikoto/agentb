import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from run_e2e_tests import print_summary  # noqa: E402
from test_cases import TestResult  # noqa: E402


class E2ESummaryTests(unittest.TestCase):
    def test_prediction_grade_is_diagnostic_not_functional_failure(self):
        result = TestResult("bridge_predict", {})
        result.ground_truth_grade = "C"
        result.predicted_grade = "A"
        result.grade_score = 20
        output = io.StringIO()

        with redirect_stdout(output):
            all_passed = print_summary([result], 1.0)

        self.assertTrue(all_passed)
        self.assertIn("PASS", output.getvalue())
        self.assertIn("Grade: A vs C (score: 20/100)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
