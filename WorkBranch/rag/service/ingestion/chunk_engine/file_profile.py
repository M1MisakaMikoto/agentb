from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class FileProfile:
    path: Path
    mime: str
    extension: str
    size: int
    text_ratio: float = 1.0
    has_images: bool = False
    page_count: Optional[int] = None
    avg_dpi: Optional[int] = None

