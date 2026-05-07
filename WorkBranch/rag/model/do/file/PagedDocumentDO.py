from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .DocumentDO import DocumentDO


@dataclass
class PagedDocumentDO:
    page: int
    size: int
    total: int
    items: List[DocumentDO]
