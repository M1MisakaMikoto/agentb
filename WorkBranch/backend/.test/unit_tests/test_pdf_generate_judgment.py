import sys
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from test_cases.pdf_generate import discard_stream_timeout_errors  # noqa: E402


class PDFGenerateJudgmentTests(unittest.TestCase):
    def test_stream_window_timeout_is_discarded_when_work_finished(self):
        errors = ["stream timeout after 600s", "unrelated error"]
        self.assertEqual(
            discard_stream_timeout_errors(errors),
            ["unrelated error"],
        )

    def test_other_errors_are_preserved(self):
        errors = ["document tool not called", "PDF not found"]
        self.assertEqual(
            discard_stream_timeout_errors(errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
