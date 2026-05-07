from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .DocumentCategoryDO import DocumentCategoryDO
from .DocumentDO import DocumentDO


@dataclass
class DocumentDetailDO:
    document: DocumentDO
    categories: List[DocumentCategoryDO]
