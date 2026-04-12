from __future__ import annotations

from typing import List

from rag.model.do.file.KnowledgeBaseDO import KnowledgeBaseDO
from rag.model.vo.file.KnowledgeBaseVO import KnowledgeBaseVO


class KnowledgeBaseAssembler:
    def to_vo(self, do: KnowledgeBaseDO) -> KnowledgeBaseVO:
        return KnowledgeBaseVO(
            id=do.id,
            name=do.name,
            description=do.description,
            created_at=do.created_at,
            updated_at=do.updated_at,
        )

    def to_list_vo(self, items: List[KnowledgeBaseDO]) -> List[KnowledgeBaseVO]:
        return [self.to_vo(item) for item in items]
