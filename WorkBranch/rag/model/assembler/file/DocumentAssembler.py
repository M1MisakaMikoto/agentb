from __future__ import annotations

from rag.model.do.file.DocumentDO import DocumentDO
from rag.model.do.file.DocumentDetailDO import DocumentDetailDO
from rag.model.do.file.PagedDocumentDO import PagedDocumentDO
from rag.model.vo.file.DocumentCategoryVO import DocumentCategoryVO
from rag.model.vo.file.DocumentDetailVO import DocumentDetailVO
from rag.model.vo.file.DocumentListItemVO import DocumentListItemVO
from rag.model.vo.file.PagedDocumentsVO import PagedDocumentsVO


class DocumentAssembler:
    def to_list_item_vo(self, row: DocumentDO) -> DocumentListItemVO:
        return DocumentListItemVO(
            id=row.id,
            display_name=row.display_name,
            filename=row.filename,
            storage_key=row.storage_key,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            status=row.status,
            updated_at=row.updated_at,
            created_at=row.created_at,
        )

    def to_paged_vo(self, page_data: PagedDocumentDO) -> PagedDocumentsVO:
        return PagedDocumentsVO(
            page=page_data.page,
            size=page_data.size,
            total=page_data.total,
            items=[self.to_list_item_vo(item) for item in page_data.items],
        )

    def to_detail_vo(self, detail: DocumentDetailDO) -> DocumentDetailVO:
        return DocumentDetailVO(
            document=self.to_list_item_vo(detail.document),
            categories=[
                DocumentCategoryVO(
                    id=cat.id,
                    name=cat.name,
                    is_primary=cat.is_primary,
                )
                for cat in detail.categories
            ],
        )
