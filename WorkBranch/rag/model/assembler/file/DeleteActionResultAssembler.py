from __future__ import annotations

from rag.model.entity.file.DeleteActionResultEntity import DeleteActionResultEntity
from rag.model.vo.file.DeleteActionResultVO import DeleteActionResultVO


class DeleteActionResultAssembler:
    def to_vo(self, payload: DeleteActionResultEntity) -> DeleteActionResultVO:
        return DeleteActionResultVO(
            ok=payload.ok,
            job_id=payload.job_id,
            document_id=payload.document_id,
            state=payload.state,
            error=payload.error,
            note=payload.note,
        )
