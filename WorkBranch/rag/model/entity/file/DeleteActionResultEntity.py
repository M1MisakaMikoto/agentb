from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DeleteActionResultEntity:
    ok: bool
    job_id: int
    document_id: int
    state: str
    error: Optional[str] = None
    note: Optional[str] = None
