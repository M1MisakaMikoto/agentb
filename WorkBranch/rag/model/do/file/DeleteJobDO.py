from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DeleteJobDO:
    id: int
    document_id: int
    storage_key: str
    collection_name: str
    state: str
    attempts: int
    last_error: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    created_at: str
    updated_at: str
