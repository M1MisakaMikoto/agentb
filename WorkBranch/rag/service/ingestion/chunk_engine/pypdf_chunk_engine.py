from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader

from .base_chunk_engine import BaseChunkEngine
from .file_profile import FileProfile


class PyPDFChunkEngine(BaseChunkEngine):
    name = "pypdf"

    def can_handle(self, profile: FileProfile) -> float:
        is_pdf = profile.extension == ".pdf" or "pdf" in profile.mime
        if not is_pdf:
            return 0.0
        # Higher text_ratio => stronger confidence this engine should be used.
        return min(1.0, 0.7 + 0.3 * max(0.0, min(profile.text_ratio, 1.0)))

    def extract_text(self, file_path: Path) -> str:
        data = file_path.read_bytes()
        reader = PdfReader(io.BytesIO(data))
        text_parts = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        return "\n".join(text_parts)
