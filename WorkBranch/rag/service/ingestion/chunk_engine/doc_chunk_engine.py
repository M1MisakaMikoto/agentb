from __future__ import annotations

from pathlib import Path

from .base_chunk_engine import BaseChunkEngine
from .file_profile import FileProfile
from .office_binary import extract_doc_text


class DocChunkEngine(BaseChunkEngine):
    name = "doc"

    _DOC_SUFFIX = {".doc"}
    _DOC_MIME_KEYWORDS = ("application/msword",)

    def can_handle(self, profile: FileProfile) -> float:
        if profile.extension in self._DOC_SUFFIX:
            return 1.0
        lowered = (profile.mime or "").lower()
        if any(k in lowered for k in self._DOC_MIME_KEYWORDS):
            return 0.95
        return 0.0

    def extract_text(self, file_path: Path) -> str:
        return extract_doc_text(file_path)
