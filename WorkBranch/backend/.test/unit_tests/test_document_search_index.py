import os
import sys

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.tools.document_tools import (
    _docx_read,
    _docx_search,
    _search_canonical_text,
)
from service.agent_service.tools import document_tools
from service.agent_service.tools.registry import ALL_TOOLS


def _assert_read_hint_matches(search_result, read_result, expected):
    assert search_result["error"] is None
    match = search_result["result"]["matches"][0]
    assert match["pattern"] == expected
    assert match["matched_text"] == expected
    assert match["char_start"] < match["char_end"]
    assert match["segment_number"] == match["segment_index"] + 1
    assert match["snippet"] == read_result["result"]["content"]
    assert expected in read_result["result"]["content"]


def test_v4_document_prompt_explains_search_index_and_read_hint():
    params = ALL_TOOLS['document']['params']

    assert 'character offsets' in params
    assert 'segment number' in params
    assert 'read_hint.start_idx' in params
    assert 'read_hint.max_length' in params
    assert 'operation r' in params


def test_canonical_search_reports_all_matches_when_results_are_limited():
    result = _search_canonical_text(
        "report.txt",
        "target one\nmiddle\ntarget two\n",
        "target",
        context=0,
        max_results=1,
    )

    assert result["error"] is None
    assert result["result"]["total_matches"] == 2
    assert result["result"]["returned_matches"] == 1
    assert result["result"]["truncated"] is True


def test_docx_table_match_can_be_read_by_returned_hint(tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "report.docx"
    document = docx.Document()
    document.add_paragraph("监测报告")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "测点"
    table.cell(0, 1).text = "累计变化"
    table.cell(1, 0).text = "BSYL-WY8"
    table.cell(1, 1).text = "-9.90mm"
    document.save(path)

    search_result = _docx_search(str(path), "BSYL-WY8", context=1)
    hint = search_result["result"]["matches"][0]["read_hint"]
    read_result = _docx_read(str(path), include_metadata=False, **hint)

    _assert_read_hint_matches(search_result, read_result, "BSYL-WY8")


def _fake_canonical_reader(full_text, metadata=None):
    def read(
        _file_path,
        start_idx=0,
        max_length=sys.maxsize,
        include_metadata=True,
        **_kwargs,
    ):
        end_idx = min(start_idx + max_length, len(full_text))
        return {
            "result": {
                "content": full_text[start_idx:end_idx],
                "metadata": metadata if include_metadata else {},
                "total_length": len(full_text),
                "read_range": f"{start_idx}-{end_idx}",
                "truncated": end_idx < len(full_text),
            },
            "error": None,
        }

    return read


def test_pdf_match_can_be_read_by_returned_hint(monkeypatch):
    full_text = "监测报告\n测点 | 累计变化\nBSYL-WY8 | -9.90mm\n"
    reader = _fake_canonical_reader(full_text)
    monkeypatch.setattr(document_tools, "_pdf_read", reader)

    search_result = document_tools._pdf_search("report.pdf", "BSYL-WY8", context=1)
    hint = search_result["result"]["matches"][0]["read_hint"]
    read_result = reader("report.pdf", include_metadata=False, **hint)

    _assert_read_hint_matches(search_result, read_result, "BSYL-WY8")


def test_excel_match_can_be_read_by_returned_hint(monkeypatch):
    full_text = "## Sheet: Sheet1\n测点 | 累计变化\nBSYL-WY8 | -9.90mm\n"
    reader = _fake_canonical_reader(
        full_text,
        metadata={"sheet_names": ["Sheet1"]},
    )
    monkeypatch.setattr(document_tools, "_excel_read", reader)

    search_result = document_tools._excel_search(
        "report.xlsx",
        "BSYL-WY8",
        context=1,
    )
    hint = search_result["result"]["matches"][0]["read_hint"]
    read_result = reader("report.xlsx", include_metadata=False, **hint)

    _assert_read_hint_matches(search_result, read_result, "BSYL-WY8")
    assert search_result["result"]["sheets_searched"] == ["Sheet1"]
