from __future__ import annotations

from pathlib import Path

from .base_chunk_engine import BaseChunkEngine
from .file_profile import FileProfile

try:
    import easyocr
except Exception:  # pragma: no cover
    easyocr = None


class OCRChunkEngine(BaseChunkEngine):
    name = "ocr"

    _IMAGE_SUFFIX = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"}

    def __init__(self) -> None:
        self._reader = easyocr.Reader(["ch_sim", "en"]) if easyocr is not None else None

    def can_handle(self, profile: FileProfile) -> float:
        if self._reader is None:
            return 0.0

        suffix_ok = profile.extension in self._IMAGE_SUFFIX
        mime_ok = profile.mime.startswith("image/")
        if suffix_ok or mime_ok:
            return 1.0

        is_pdf = profile.extension == ".pdf" or "pdf" in profile.mime
        if is_pdf:
            # lower text_ratio => more likely scanned PDF
            return max(0.0, 1.0 - max(0.0, min(profile.text_ratio, 1.0)))
        return 0.0

    def extract_text(self, file_path: Path) -> str:
        if self._reader is None:
            return ""
        result = self._reader.readtext(str(file_path), detail=0, paragraph=True)
        return "\n".join(result)
