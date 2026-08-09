"""Markdown → HTML → WeasyPrint → PDF 渲染器。

替代旧的 docx→soffice / docx→xelatex 链路：CSS 完全控制排版，
支持中文（Noto Sans CJK）、标题层级、表格、页眉页脚、页码。
"""

from __future__ import annotations

import os
from typing import Optional

import markdown as md
from weasyprint import HTML


_CSS = """
@page {
  size: A4;
  margin: 2.2cm 2cm 2.2cm 2cm;
  @top-center {
    content: string(doctitle);
    font-size: 9pt;
    color: #7a8699;
  }
  @bottom-center {
    content: "第 " counter(page) " 页 / 共 " counter(pages) " 页";
    font-size: 9pt;
    color: #7a8699;
  }
}
body {
  font-family: "Noto Sans CJK SC", "Noto Sans CJK", sans-serif;
  font-size: 11pt;
  line-height: 1.7;
  color: #1f2430;
}
h1 {
  string-set: doctitle content();
  font-size: 20pt;
  text-align: center;
  margin: 0.5em 0 0.9em;
  padding-bottom: 0.4em;
  border-bottom: 2px solid #2456e6;
}
h2 {
  font-size: 15pt;
  color: #17327a;
  border-left: 4px solid #2456e6;
  padding-left: 0.4em;
  margin: 1.2em 0 0.5em;
}
h3 {
  font-size: 13pt;
  color: #2456e6;
  margin: 1em 0 0.4em;
}
p {
  margin: 0.45em 0;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
  font-size: 10.5pt;
}
th, td {
  border: 1px solid #c9d2e0;
  padding: 6px 10px;
  text-align: left;
}
th {
  background: #eef3ff;
  font-weight: 600;
}
tr:nth-child(even) td {
  background: #f7f9fc;
}
code {
  background: #eef1f6;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 10pt;
}
pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 10px 14px;
  border-radius: 6px;
  overflow-x: auto;
}
blockquote {
  border-left: 4px solid #2456e6;
  background: #eef3ff;
  margin: 0.8em 0;
  padding: 8px 14px;
  color: #334155;
}
li {
  margin: 0.2em 0;
}
hr {
  border: none;
  border-top: 1px solid #dbe2ec;
  margin: 1em 0;
}
"""


def render_markdown_to_pdf(
    markdown_text: str,
    pdf_path: str,
    metadata: Optional[dict] = None,
) -> dict:
    """将 Markdown 渲染为 PDF。

    Returns:
        {"message": ..., "pdf_path": ..., "size": ...}
    """
    meta = metadata or {}
    title = str(meta.get("title") or "")
    body = md.markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    html = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>{_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
    HTML(string=html).write_pdf(pdf_path)
    assert os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0
    return {
        "message": f"PDF创建成功: {pdf_path}",
        "pdf_path": pdf_path,
        "size": os.path.getsize(pdf_path),
    }
