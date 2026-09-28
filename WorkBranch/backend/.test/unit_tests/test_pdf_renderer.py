import os
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from service.agent_service.tools.pdf_renderer import render_markdown_to_pdf  # noqa: E402
from service.agent_service.tools import document_tools  # noqa: E402
from service.agent_service.tools.pdf_renderer import (  # noqa: E402
    build_html,
    render_markdown_to_pdf_via_libreoffice,
)


MARKDOWN = (
    "# 桥梁定期检查报告\n\n## 工程概况\n\n正文内容。\n\n"
    "| 项目 | 值 |\n|------|----|\n| BCI | 81.85 |\n"
)


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401

        return True
    except Exception:
        return False


class PDFRendererTests(unittest.TestCase):
    def test_build_html_renders_markdown_structure(self):
        """Markdown → HTML 与渲染后端无关，任何环境都必须通过。"""
        html = build_html(MARKDOWN, {"title": "桥梁定期检查报告"})
        self.assertIn("<h1", html)
        self.assertIn("<h2", html)
        self.assertIn("<table>", html)
        self.assertIn("桥梁定期检查报告", html)
        self.assertNotIn("# 桥梁", html)

    def test_renders_markdown_to_valid_pdf_with_available_backend(self):
        """按可用后端渲染 PDF：优先 LibreOffice，其次 WeasyPrint；都没有则跳过。"""
        soffice = document_tools._find_libreoffice()
        if soffice:
            render = lambda text, path, meta: render_markdown_to_pdf_via_libreoffice(
                text, path, meta, soffice, 120
            )
        elif _weasyprint_available():
            render = render_markdown_to_pdf
        else:
            self.skipTest("本机既无 LibreOffice 也无 WeasyPrint 运行库（GTK）")

        tmp_pdf = Path(BACKEND_DIR) / "data" / "pdf_renderer_test.pdf"
        try:
            result = render(MARKDOWN, str(tmp_pdf), {"title": "桥梁定期检查报告"})
            self.assertIsNone(result.get("error"), result)
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
