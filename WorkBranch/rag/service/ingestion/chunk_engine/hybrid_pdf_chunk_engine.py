from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

from .base_chunk_engine import BaseChunkEngine
from .file_profile import FileProfile

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

try:
    import easyocr
except Exception:  # pragma: no cover
    easyocr = None

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


class HybridPDFChunkEngine(BaseChunkEngine):
    """
    Mixed routing for PDF:
    - page has enough text -> use pypdf text layer
    - page has low text -> OCR fallback (if OCR runtime available)
    """

    name = "hybrid_pdf"

    def __init__(self, min_text_chars: int = 80) -> None:
        self.min_text_chars = min_text_chars
        self._ocr_reader = None
        if easyocr is not None and fitz is not None and np is not None:
            try:
                self._ocr_reader = easyocr.Reader(["ch_sim", "en"])
            except Exception:
                self._ocr_reader = None

    def can_handle(self, profile: FileProfile) -> float:
        is_pdf = profile.extension == ".pdf" or "pdf" in profile.mime
        if not is_pdf:
            return 0.0
        # Always prefer this engine for PDF; it includes page-level fallback.
        return 1.0

    def extract_text(self, file_path: Path) -> str:
        chunks = self.chunk(file_path)
        return "\n".join(chunks)

    def _ocr_page(self, file_path: Path, page_index: int) -> str:
        if self._ocr_reader is None or fitz is None or np is None:
            return ""
        try:
            with fitz.open(str(file_path)) as doc:
                if page_index >= len(doc):
                    return ""
                page = doc[page_index]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                if pix.n == 4:
                    arr = arr[:, :, :3]
                result = self._ocr_reader.readtext(arr, detail=0, paragraph=True)
                return "\n".join(result).strip()
        except Exception:
            return ""

    def chunk(self, file_path: Path) -> list[str]:
        data = file_path.read_bytes()
        reader = PdfReader(io.BytesIO(data))
        out: list[str] = []
        for idx, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if len(text) >= self.min_text_chars:
                out.append(f"[page:{idx + 1}] {text}")
                continue

            ocr_text = self._ocr_page(file_path=file_path, page_index=idx)
            if ocr_text:
                out.append(f"[page:{idx + 1}] {ocr_text}")
            elif text:
                out.append(f"[page:{idx + 1}] {text}")
        return out

