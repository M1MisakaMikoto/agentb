from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class IngestJobDO:
    id: int
    document_id: int
    status: str
    error_message: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    created_at: str
