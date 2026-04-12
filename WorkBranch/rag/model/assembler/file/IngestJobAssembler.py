from __future__ import annotations

from rag.model.do.file.IngestJobDO import IngestJobDO
from rag.model.vo.file.IngestJobVO import IngestJobVO


class IngestJobAssembler:
    def to_vo(self, row: IngestJobDO) -> IngestJobVO:
        return IngestJobVO(
            id=row.id,
            document_id=row.document_id,
            status=row.status,
            error_message=row.error_message,
            started_at=row.started_at,
            finished_at=row.finished_at,
            created_at=row.created_at,
        )
