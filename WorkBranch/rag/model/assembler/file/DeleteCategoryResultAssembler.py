from __future__ import annotations

from rag.model.do.file.DeleteCategoryResultDO import DeleteCategoryResultDO
from rag.model.vo.file.DeleteCategoryResultVO import DeleteCategoryResultVO


class DeleteCategoryResultAssembler:
    def to_vo(self, result: DeleteCategoryResultDO) -> DeleteCategoryResultVO:
        return DeleteCategoryResultVO(
            ok=result.ok,
            id=result.id,
            mode=result.mode,
            deleted_categories=result.deleted_categories,
        )
