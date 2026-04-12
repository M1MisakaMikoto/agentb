from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DocumentDO:
    id: int
    display_name: str
    filename: str
    storage_key: str
    mime_type: str
    size_bytes: int
    status: str
    updated_at: str
    created_at: str
    kb_id: Optional[int] = field(default=None)
