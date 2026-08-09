import os
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from service.agent_service.tools.pdf_renderer import render_markdown_to_pdf  # noqa: E402


class PDFRendererTests(unittest.TestCase):
    def test_renders_markdown_to_valid_pdf(self):
        tmp_pdf = Path(BACKEND_DIR) / "data" / "pdf_renderer_test.pdf"
        try:
            result = render_markdown_to_pdf(
                "# 桥梁定期检查报告\n\n## 工程概况\n\n正文内容。\n\n"
                "| 项目 | 值 |\n|------|----|\n| BCI | 81.85 |\n",
                str(tmp_pdf),
                {"title": "桥梁定期检查报告"},
            )
            self.assertIsNone(result.get("error"))
            self.assertTrue(tmp_pdf.exists())
            self.assertGreater(result["size"], 0)

            import pypdf
            reader = pypdf.PdfReader(str(tmp_pdf))
            self.assertGreaterEqual(len(reader.pages), 1)
            text = reader.pages[0].extract_text() or ""
            self.assertIn("桥梁定期检查报告", text)
            self.assertNotIn("**", text)
            self.assertNotIn("# 桥梁", text)
        finally:
            if tmp_pdf.exists():
                tmp_pdf.unlink()


if __name__ == "__main__":
    unittest.main()
