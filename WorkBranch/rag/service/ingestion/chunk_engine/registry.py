from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .base_chunk_engine import BaseChunkEngine
from .docx_chunk_engine import DocxChunkEngine
from .file_profile import FileProfile
from .file_profile_detector import FileProfileDetector
from .hybrid_pdf_chunk_engine import HybridPDFChunkEngine
from .ocr_chunk_engine import OCRChunkEngine
from .plain_text_chunk_engine import PlainTextChunkEngine
from .pypdf_chunk_engine import PyPDFChunkEngine


class ChunkEngineRegistry:
    def __init__(self, engines: Iterable[BaseChunkEngine] | None = None) -> None:
        self.detector = FileProfileDetector()
        self.engines: List[BaseChunkEngine] = list(engines or [
            HybridPDFChunkEngine(),
            PyPDFChunkEngine(),
            OCRChunkEngine(),
            DocxChunkEngine(),
            PlainTextChunkEngine(),
        ])

    def select(self, file_path: Path, mime_type: str | None) -> tuple[BaseChunkEngine, FileProfile]:
        profile = self.detector.detect(file_path=file_path, mime_type_hint=mime_type)

        scored = []
        for engine in self.engines:
            score = engine.can_handle(profile)
            scored.append((score, engine))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_engine = scored[0]
        if best_score <= 0:
            best_engine = PlainTextChunkEngine()
        return best_engine, profile
