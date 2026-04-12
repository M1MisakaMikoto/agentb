from __future__ import annotations

from pathlib import Path

from .base_chunk_engine import BaseChunkEngine
from .file_profile import FileProfile


class PlainTextChunkEngine(BaseChunkEngine):
    name = "plain_text"

    _TEXT_SUFFIX = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".log"}

    def can_handle(self, profile: FileProfile) -> float:
        suffix_ok = profile.extension in self._TEXT_SUFFIX
        mime_ok = profile.mime.startswith("text/")
        if suffix_ok or mime_ok:
            return 1.0
        # fallback for unknown mime but tiny plain files
        if profile.size > 0 and profile.size < 2 * 1024 * 1024:
            return 0.15
        return 0.0

    def extract_text(self, file_path: Path) -> str:
        return file_path.read_text(encoding="utf-8", errors="replace")
