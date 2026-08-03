from __future__ import annotations

from pathlib import Path

from .base_chunk_engine import BaseChunkEngine
from .file_profile import FileProfile
from .office_binary import extract_ppt_text


class PptChunkEngine(BaseChunkEngine):
    """Legacy binary .ppt fallback: OLE + UTF-16LE extraction (no external deps)."""

    name = "ppt"

    _PPT_SUFFIX = {".ppt"}
    _PPT_MIME_KEYWORDS = ("application/vnd.ms-powerpoint", "application/mspowerpoint")

    def can_handle(self, profile: FileProfile) -> float:
        if profile.extension in self._PPT_SUFFIX:
            return 1.0
        lowered = (profile.mime or "").lower()
        if any(k in lowered for k in self._PPT_MIME_KEYWORDS):
            return 0.95
        return 0.0

    def extract_text(self, file_path: Path) -> str:
        return extract_ppt_text(file_path)
