from __future__ import annotations

from pathlib import Path

from .base_chunk_engine import BaseChunkEngine
from .file_profile import FileProfile
from .office_binary import extract_xls_text


class XlsChunkEngine(BaseChunkEngine):
    name = "xls"

    _SUFFIX = {".xls"}
    _MIME_KEYWORDS = ("application/vnd.ms-excel", "application/msexcel")

    def can_handle(self, profile: FileProfile) -> float:
        if profile.extension in self._SUFFIX:
            return 1.0
        lowered = (profile.mime or "").lower()
        if any(k in lowered for k in self._MIME_KEYWORDS):
            return 0.95
        return 0.0

    def extract_text(self, file_path: Path) -> str:
        return extract_xls_text(file_path)
