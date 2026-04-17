from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from rag.DAO.file_meta_dao import FileMetaDAO
from rag.DAO.knowledge_base_dao import KnowledgeBaseDAO
from rag.logging_utils import get_logger
from rag.model.assembler.file.CategoryTreeAssembler import CategoryTreeAssembler
from rag.model.assembler.file.DeleteActionResultAssembler import DeleteActionResultAssembler
from rag.model.assembler.file.DeleteJobAssembler import DeleteJobAssembler
from rag.model.assembler.file.DeleteCategoryResultAssembler import DeleteCategoryResultAssembler
from rag.model.assembler.file.DocumentAssembler import DocumentAssembler
from rag.model.assembler.file.FileResponseAssembler import FileResponseAssembler
from rag.model.assembler.file.IngestJobAssembler import IngestJobAssembler
from rag.model.assembler.file.KnowledgeBaseAssembler import KnowledgeBaseAssembler
from rag.model.dto.file.CategoryCreateRequestDTO import CategoryCreateRequestDTO
from rag.model.dto.file.CategoryUpdateRequestDTO import CategoryUpdateRequestDTO
from rag.model.dto.file.CreateFileRequestDTO import CreateFileRequestDTO
from rag.model.dto.file.DocumentUpdateRequestDTO import DocumentUpdateRequestDTO
from rag.model.dto.file.KnowledgeBaseCreateRequestDTO import KnowledgeBaseCreateRequestDTO
from rag.model.dto.file.KnowledgeBaseUpdateRequestDTO import KnowledgeBaseUpdateRequestDTO
from rag.model.dto.file.UpdateFileRequestDTO import UpdateFileRequestDTO
from rag.model.vo.file.CategoryMutationVO import CategoryMutationVO
from rag.model.vo.file.DeleteCategoryResultVO import DeleteCategoryResultVO
from rag.model.vo.file.DocumentCategoryBindVO import DocumentCategoryBindVO
from rag.model.vo.file.DocumentUploadVO import DocumentUploadVO
from rag.model.vo.file.IdResultVO import IdResultVO
from rag.model.vo.file.KnowledgeBaseMutationVO import KnowledgeBaseMutationVO
from rag.service.document_delete_service import DocumentDeleteService
from rag.service.file_system_service import FileSystemService
from rag.service.ingestion import IngestionService

APP_ROOT = Path(__file__).resolve().parents[3]  # D:\project_hub\agentb
RAG_ROOT = Path(__file__).resolve().parents[1]  # ...\WorkBranch\rag
DOCS_ROOT = (APP_ROOT / "DOCS").resolve()
MANAGED_ROOT = (DOCS_ROOT / "raw").resolve()
UI_PATH = (RAG_ROOT / "ui" / "file_manager.html").resolve()
META_DB = (RAG_ROOT / "file_meta.sqlite3").resolve()
FILE_META_DAO = FileMetaDAO(META_DB)
KNOWLEDGE_BASE_DAO = KnowledgeBaseDAO(META_DB)
FILE_SYSTEM_SERVICE = FileSystemService(MANAGED_ROOT)
CATEGORY_TREE_ASSEMBLER = CategoryTreeAssembler()
DELETE_ACTION_RESULT_ASSEMBLER = DeleteActionResultAssembler()
DELETE_JOB_ASSEMBLER = DeleteJobAssembler()
DELETE_CATEGORY_RESULT_ASSEMBLER = DeleteCategoryResultAssembler()
DOCUMENT_ASSEMBLER = DocumentAssembler()
FILE_RESPONSE_ASSEMBLER = FileResponseAssembler()
INGEST_JOB_ASSEMBLER = IngestJobAssembler()
KNOWLEDGE_BASE_ASSEMBLER = KnowledgeBaseAssembler()
LOGGER = get_logger(__name__)

# --- IngestionService 模块级单例（模型只加载一次，避免每次请求重建）---
_INGESTION_SERVICE: Optional[IngestionService] = None


def _get_ingestion_service() -> IngestionService:
    global _INGESTION_SERVICE
    if _INGESTION_SERVICE is None:
        _INGESTION_SERVICE = IngestionService()
    return _INGESTION_SERVICE


MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB

router = APIRouter(prefix="/rag", tags=["rag"])


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_abs(storage_key: str) -> Path:
    # Backward compatible: old rows may store `raw/<name>`.
    key = storage_key.replace("\\", "/").lstrip("/")
    if key.startswith("raw/"):
        return (DOCS_ROOT / key).resolve()
    return (MANAGED_ROOT / key).resolve()


def _ensure_schema() -> None:
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    MANAGED_ROOT.mkdir(parents=True, exist_ok=True)
    FILE_META_DAO.ensure_schema()


def on_rag_startup() -> None:
    """由 agentb app.py 的 lifespan 调用。"""
    _ensure_schema()


@router.get("/")
def ui() -> FileResponse:
    if not UI_PATH.exists():
        raise HTTPException(status_code=404, detail="UI file not found")
    return FileResponse(UI_PATH)


# ------------------------
# Knowledge Bases（知识库 CRUD）
# ------------------------
@router.get("/api/knowledge-bases")
def list_knowledge_bases() -> dict:
    items = KNOWLEDGE_BASE_DAO.list_all()
    return {"items": [vo.model_dump() for vo in KNOWLEDGE_BASE_ASSEMBLER.to_list_vo(items)]}


@router.post("/api/knowledge-bases")
def create_knowledge_base(payload: KnowledgeBaseCreateRequestDTO) -> dict:
    try:
        kb_id = KNOWLEDGE_BASE_DAO.create(payload.name, payload.description)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return KnowledgeBaseMutationVO(ok=True, id=kb_id).model_dump()


@router.put("/api/knowledge-bases/{kb_id}")
def update_knowledge_base(kb_id: int, payload: KnowledgeBaseUpdateRequestDTO) -> dict:
    try:
        KNOWLEDGE_BASE_DAO.update(kb_id, payload.name, payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return KnowledgeBaseMutationVO(ok=True, id=kb_id).model_dump()


@router.delete("/api/knowledge-bases/{kb_id}")
def delete_knowledge_base(kb_id: int) -> dict:
    try:
        KNOWLEDGE_BASE_DAO.delete(kb_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return KnowledgeBaseMutationVO(ok=True, id=kb_id).model_dump()


# ------------------------
# Categories (virtual directory)
# ------------------------
@router.get("/api/categories/tree")
def categories_tree() -> dict:
    rows = FILE_META_DAO.list_categories()
    vo = CATEGORY_TREE_ASSEMBLER.to_tree_response(rows)
    return vo.model_dump()


@router.post("/api/categories")
def create_category(payload: CategoryCreateRequestDTO) -> dict:
    try:
        category_id = FILE_META_DAO.create_category(payload.name, payload.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return CategoryMutationVO(ok=True, id=category_id).model_dump()


@router.put("/api/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdateRequestDTO) -> dict:
    try:
        FILE_META_DAO.update_category(category_id, payload.name, payload.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        message = str(exc)
        status = 400 if "parent_id cannot be self" in message else 409
        raise HTTPException(status_code=status, detail=message)
    return CategoryMutationVO(ok=True, id=category_id).model_dump()


@router.delete("/api/categories/{category_id}")
def delete_category(
    category_id: int,
    mode: Literal["keep_docs", "unbind_docs", "recursive"] = Query(default="keep_docs"),
) -> dict:
    try:
        result_do = FILE_META_DAO.delete_category(category_id, mode)
        result_vo: DeleteCategoryResultVO = DELETE_CATEGORY_RESULT_ASSEMBLER.to_vo(result_do)
        return result_vo.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ------------------------
# Documents + mapping
# ------------------------
@router.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    category_id: Optional[int] = Form(default=None),
    kb_id: Optional[int] = Form(default=None),
) -> dict:
    content = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 100 MB)")
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    size = len(content)
    hash_sha = _sha256_bytes(content)
    display_name = file.filename or "unnamed"
    LOGGER.info(
        "upload_received filename=%s category_id=%s kb_id=%s size_bytes=%s mime=%s sha256=%s",
        display_name,
        category_id,
        kb_id,
        size,
        mime,
        hash_sha,
    )

    try:
        document_create = FILE_META_DAO.create_document(
            display_name=display_name,
            mime=mime,
            size=size,
            hash_sha=hash_sha,
            category_id=category_id,
            kb_id=kb_id,
        )
        doc_id = document_create.id
        storage_key = document_create.storage_key
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    target = _storage_abs(storage_key)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        LOGGER.info(
            "upload_saved document_id=%s storage_key=%s save_path=%s",
            doc_id,
            storage_key,
            str(target),
        )
    except Exception as exc:
        # Compensate metadata already committed before file write.
        FILE_META_DAO.delete_document_row(doc_id)
        LOGGER.exception(
            "upload_save_failed document_id=%s storage_key=%s save_path=%s error=%s",
            doc_id,
            storage_key,
            str(target),
            str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Failed to persist uploaded file: {exc}")

    # collection_name 由 IngestionService 根据 doc ctx 中的 kb_id 自动推导，无需外传
    # 同步 CPU 密集型推理放入线程池，避免阻塞 asyncio event loop
    ingest_result = await asyncio.get_running_loop().run_in_executor(
        None, lambda: _get_ingestion_service().ingest_document(document_id=doc_id)
    )
    LOGGER.info(
        "upload_ingest_result document_id=%s storage_key=%s ingest_ok=%s ingest_job_id=%s chunk_count=%s error=%s",
        doc_id,
        storage_key,
        ingest_result.get("ok"),
        ingest_result.get("job_id"),
        ingest_result.get("chunk_count"),
        ingest_result.get("error"),
    )
    return DocumentUploadVO(
        ok=True,
        id=doc_id,
        storage_key=storage_key,
        ingest=ingest_result,
    ).model_dump()


@router.get("/api/documents")
def list_documents(
    category_id: Optional[int] = None,
    keyword: str = "",
    page: int = 1,
    size: int = 20,
) -> dict:
    page = max(1, page)
    size = min(100, max(1, size))
    page_do = FILE_META_DAO.list_documents(
        category_id=category_id,
        keyword=keyword,
        page=page,
        size=size,
    )
    return DOCUMENT_ASSEMBLER.to_paged_vo(page_do).model_dump()


@router.get("/api/documents/{document_id}")
def get_document(document_id: int) -> dict:
    try:
        detail_do = FILE_META_DAO.get_document_detail(document_id)
        return DOCUMENT_ASSEMBLER.to_detail_vo(detail_do).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/api/documents/{document_id}")
def update_document(document_id: int, payload: DocumentUpdateRequestDTO) -> dict:
    try:
        FILE_META_DAO.update_document_name(document_id, payload.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return IdResultVO(ok=True, id=document_id).model_dump()


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: int) -> dict:
    # 知识库隔离：从文档元数据中读�?kb_id 推导 collection_name
    try:
        detail = FILE_META_DAO.get_document_detail(document_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Document not found")
    doc_kb_id = getattr(detail.document, "kb_id", None)
    collection_name = KnowledgeBaseDAO.get_collection_name(doc_kb_id)

    delete_service = DocumentDeleteService(meta_db=META_DB, docs_root=DOCS_ROOT)
    try:
        result = delete_service.delete_document(document_id=document_id, collection_name=collection_name)
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error or "Delete job failed")
        return DELETE_ACTION_RESULT_ASSEMBLER.to_vo(result).model_dump()
    except ValueError:
        raise HTTPException(status_code=404, detail="Document not found")
    finally:
        delete_service.close()


@router.get("/api/delete-jobs/{job_id}")
def get_delete_job(job_id: int) -> dict:
    delete_service = DocumentDeleteService(meta_db=META_DB, docs_root=DOCS_ROOT)
    try:
        return DELETE_JOB_ASSEMBLER.to_vo(delete_service.get_delete_job(job_id)).model_dump()
    except ValueError:
        raise HTTPException(status_code=404, detail="Delete job not found")
    finally:
        delete_service.close()


@router.post("/api/delete-jobs/{job_id}/retry")
def retry_delete_job(job_id: int) -> dict:
    delete_service = DocumentDeleteService(meta_db=META_DB, docs_root=DOCS_ROOT)
    try:
        return DELETE_ACTION_RESULT_ASSEMBLER.to_vo(delete_service.retry_delete_job(job_id)).model_dump()
    except ValueError:
        raise HTTPException(status_code=404, detail="Delete job not found")
    finally:
        delete_service.close()


@router.post("/api/documents/{document_id}/categories/{category_id}")
def mount_document_category(document_id: int, category_id: int) -> dict:
    try:
        FILE_META_DAO.mount_document_category(document_id, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return DocumentCategoryBindVO(ok=True, document_id=document_id, category_id=category_id).model_dump()


@router.delete("/api/documents/{document_id}/categories/{category_id}")
def unmount_document_category(document_id: int, category_id: int) -> dict:
    FILE_META_DAO.unmount_document_category(document_id, category_id)
    return DocumentCategoryBindVO(ok=True, document_id=document_id, category_id=category_id).model_dump()


@router.put("/api/documents/{document_id}/primary-category/{category_id}")
def set_primary_category(document_id: int, category_id: int) -> dict:
    try:
        FILE_META_DAO.set_primary_category(document_id, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return DocumentCategoryBindVO(ok=True, document_id=document_id, category_id=category_id).model_dump()


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    try:
        job_do = FILE_META_DAO.get_ingest_job(job_id)
        return INGEST_JOB_ASSEMBLER.to_vo(job_do).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ------------------------
# Compatibility file CRUD endpoints (physical path under DOCS/)
# ------------------------
@router.get("/api/files")
def list_files(path: str = Query(default="", description="Relative directory path")) -> dict:
    try:
        payload = FILE_SYSTEM_SERVICE.list_files(path=path)
        return FILE_RESPONSE_ASSEMBLER.to_list_vo(payload).model_dump()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/file")
def read_file(path: str = Query(..., description="Relative file path")) -> dict:
    try:
        payload = FILE_SYSTEM_SERVICE.read_file(path=path)
        return FILE_RESPONSE_ASSEMBLER.to_read_vo(payload).model_dump()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/file")
def create_file(payload: CreateFileRequestDTO) -> dict:
    try:
        raw = FILE_SYSTEM_SERVICE.create_file(
            path=payload.path,
            item_type=payload.type,
            content=payload.content,
            overwrite=payload.overwrite,
        )
        return FILE_RESPONSE_ASSEMBLER.to_mutation_vo(raw).model_dump()
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/api/file")
def update_file(payload: UpdateFileRequestDTO) -> dict:
    try:
        raw = FILE_SYSTEM_SERVICE.update_file(path=payload.path, content=payload.content)
        return FILE_RESPONSE_ASSEMBLER.to_mutation_vo(raw).model_dump()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/file")
def delete_file(path: str = Query(..., description="Relative file or directory path")) -> dict:
    try:
        raw = FILE_SYSTEM_SERVICE.delete_path(path=path)
        return FILE_RESPONSE_ASSEMBLER.to_mutation_vo(raw).model_dump()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))