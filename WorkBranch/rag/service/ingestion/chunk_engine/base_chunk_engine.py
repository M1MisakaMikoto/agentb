from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from .file_profile import FileProfile


class BaseChunkEngine(ABC):
    """Extract + split text chunks from source file."""

    name: str = "base"

    @abstractmethod
    def can_handle(self, profile: FileProfile) -> float:
        raise NotImplementedError

    @abstractmethod
    def extract_text(self, file_path: Path) -> str:
        raise NotImplementedError

    def split_text(self, text: str, chunk_size: int = 800, overlap: int = 120) -> List[str]:
        clean = (text or "").strip()
        if not clean:
            return []

        chunks: List[str] = []
        start = 0
        n = len(clean)
        while start < n:
            end = min(start + chunk_size, n)
            chunk = clean[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end == n:
                break
            start = max(0, end - overlap)
        return chunks

    def chunk(self, file_path: Path) -> List[str]:
        text = self.extract_text(file_path)
        return self.split_text(text)
