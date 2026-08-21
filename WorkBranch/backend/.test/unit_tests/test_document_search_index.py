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

    assert 'LINE_NUM|CONTENT' in params
    assert 'total_lines' in params
    assert 'file_size' in params
    assert 'next_start_idx' in params
    assert 'start_idx and max_length' in params
    assert 'offset' in params
    assert 'limit' in params
    assert 'complete line' in params
    assert 'character offsets' in params
    assert 'segment number' in params
    assert 'read_hint.start_idx' in params
    assert 'read_hint.max_length' in params
    assert 'operation r' in params
    assert 'one result per matching line' in params
    assert 'occurrences' in params
    assert 'next_start_idx' in params


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
    assert result["result"]["total_occurrences"] == 2
    assert result["result"]["returned_matches"] == 1
    assert result["result"]["next_start_idx"] == len("target one\n")
    assert "truncated" not in result["result"]


def test_canonical_search_groups_all_occurrences_on_each_matching_line():
    result = _search_canonical_text(
        "report.txt",
        "位移和累计位移都需要检查\n下一行没有结果\n",
        "位移|累计",
        context=0,
    )

    assert result["error"] is None
    data = result["result"]
    assert data["total_matches"] == 1
    assert data["total_occurrences"] == 3
    assert data["returned_matches"] == 1
    assert data["matches"][0]["matched_texts"] == ["位移", "累计", "位移"]
    assert len(data["matches"][0]["occurrences"]) == 3
    assert data["matches"][0]["segment_number"] == 1


def test_canonical_search_pages_by_next_start_idx_without_overlap():
    full_text = "target one\nmiddle\ntarget two\ntarget three\n"
    first = _search_canonical_text(
        "report.txt",
        full_text,
        "target",
        context=0,
        max_results=1,
    )["result"]
    second = _search_canonical_text(
        "report.txt",
        full_text,
        "target",
        context=0,
        max_results=1,
        start_idx=first["next_start_idx"],
    )["result"]
    third = _search_canonical_text(
        "report.txt",
        full_text,
        "target",
        context=0,
        max_results=1,
        start_idx=second["next_start_idx"],
    )["result"]

    assert first["total_matches"] == second["total_matches"] == third["total_matches"] == 3
    assert [
        first["matches"][0]["segment_number"],
        second["matches"][0]["segment_number"],
        third["matches"][0]["segment_number"],
    ] == [1, 3, 4]
    assert first["search_start_idx"] == 0
    assert second["search_start_idx"] == len("target one\n")
    assert third["next_start_idx"] is None


def test_canonical_search_rejects_negative_start_idx():
    result = _search_canonical_text(
        "report.txt",
        "target\n",
        "target",
        start_idx=-1,
    )

    assert result["error"] == "start_idx 不能小于 0"


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
