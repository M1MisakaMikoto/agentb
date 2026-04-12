from __future__ import annotations

import io
import mimetypes
from pathlib import Path

from pypdf import PdfReader

from .file_profile import FileProfile

try:
    import magic  # type: ignore
except Exception:  # pragma: no cover
    magic = None


class FileProfileDetector:
    def detect(self, file_path: Path, mime_type_hint: str | None = None) -> FileProfile:
        mime = mime_type_hint or self._detect_mime(file_path)
        extension = file_path.suffix.lower()
        size = file_path.stat().st_size if file_path.exists() else 0
        text_ratio = 1.0
        page_count = None

        if "pdf" in mime or extension == ".pdf":
            text_ratio, page_count = self._probe_pdf_text_ratio(file_path)

        return FileProfile(
            path=file_path,
            mime=mime,
            extension=extension,
            size=size,
            text_ratio=text_ratio,
            page_count=page_count,
        )

    def _detect_mime(self, file_path: Path) -> str:
        if magic is not None:
            try:
                return str(magic.from_file(str(file_path), mime=True))
            except Exception:
                pass
        guessed, _ = mimetypes.guess_type(str(file_path))
        return guessed or "application/octet-stream"

    def _probe_pdf_text_ratio(self, file_path: Path) -> tuple[float, int | None]:
        try:
            data = file_path.read_bytes()
            reader = PdfReader(io.BytesIO(data))
            page_count = len(reader.pages)
            if page_count == 0:
                return 0.0, 0

            # Sample first up to 3 pages to avoid heavy full scan
            sample_pages = min(3, page_count)
            total_chars = 0
            for i in range(sample_pages):
                text = reader.pages[i].extract_text() or ""
                total_chars += len(text.strip())

            # Heuristic: 500 chars as "good text layer" baseline for sampled pages
            ratio = min(total_chars / 500.0, 1.0)
            return ratio, page_count
        except Exception:
            return 0.0, None

