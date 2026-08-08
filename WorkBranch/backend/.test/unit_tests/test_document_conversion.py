import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from service.agent_service.tools import document_tools


def test_libreoffice_doc_conversion_reads_from_output_directory(tmp_path: Path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    source = source_dir / "report.doc"
    source.write_bytes(b"legacy-doc")
    target = output_dir / "random.docx"
    generated = output_dir / "report.docx"

    def fake_run(command, **kwargs):
        assert command[-2] == str(output_dir)
        assert command[-1] == str(source)
        generated.write_bytes(b"converted-docx")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    with (
        patch.object(document_tools.sys, "platform", "linux"),
        patch.object(document_tools.tempfile, "mktemp", return_value=str(target)),
        patch.dict(sys.modules, {"docx2python": None}),
        patch.object(document_tools.subprocess, "run", side_effect=fake_run),
    ):
        actual = document_tools._convert_doc_to_docx(str(source))

    assert actual == str(target)
    assert target.read_bytes() == b"converted-docx"
    assert not generated.exists()
