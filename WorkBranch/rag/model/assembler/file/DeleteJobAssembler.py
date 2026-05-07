from __future__ import annotations

from rag.model.do.file.DeleteJobDO import DeleteJobDO
from rag.model.vo.file.DeleteJobVO import DeleteJobVO


class DeleteJobAssembler:
    def to_vo(self, payload: DeleteJobDO) -> DeleteJobVO:
        return DeleteJobVO(
            id=payload.id,
            document_id=payload.document_id,
            storage_key=payload.storage_key,
            collection_name=payload.collection_name,
            state=payload.state,
            attempts=payload.attempts,
            last_error=payload.last_error,
            started_at=payload.started_at,
            finished_at=payload.finished_at,
            created_at=payload.created_at,
            updated_at=payload.updated_at,
        )
