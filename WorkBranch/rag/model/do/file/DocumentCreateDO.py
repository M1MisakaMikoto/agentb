from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DocumentCreateDO:
    id: int
    storage_key: str
