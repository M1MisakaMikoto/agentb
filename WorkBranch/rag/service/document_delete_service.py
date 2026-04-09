from __future__ import annotations

from pathlib import Path
from typing import Optional

from rag.DAO.delete_job_dao import DeleteJobDAO
from rag.DAO.document_lifecycle_dao import DocumentLifecycleDAO
from rag.DAO.RAG_DAO import RAG_DAO
from rag.model.do.file.DeleteJobDO import DeleteJobDO
from rag.model.entity.file.DeleteActionResultEntity import DeleteActionResultEntity
from rag.service.delete_state import DeleteState


class DeleteStateConflictError(Exception):
    """Raised when state transition fails due to concurrent update."""


class DocumentDeleteService:
    """WAL-like delete workflow with resumable state machine."""

    def __init__(
        self,
        meta_db: Path,
        docs_root: Path,
        rag_dao: Optional[RAG_DAO] = None,
        delete_job_dao: Optional[DeleteJobDAO] = None,
        lifecycle_dao: Optional[DocumentLifecycleDAO] = None,
    ) -> None:
        self.docs_root = docs_root
        self.rag_dao = rag_dao or RAG_DAO()
        self.delete_job_dao = delete_job_dao or DeleteJobDAO(db_path=meta_db)
        self.lifecycle_dao = lifecycle_dao or DocumentLifecycleDAO(db_path=meta_db)

    def _storage_abs(self, storage_key: str) -> Path:
        key = storage_key.replace("\\", "/").lstrip("/")
        if key.startswith("raw/"):
            return (self.docs_root / key).resolve()
        return (self.docs_root / "raw" / key).resolve()

    def _create_or_get_job(self, document_id: int, storage_key: str, collection_name: str) -> int:
        active_job_id = self.delete_job_dao.find_active_job_id(document_id)
        if active_job_id is not None:
            return active_job_id
        return self.delete_job_dao.create_job(
            document_id=document_id,
            storage_key=storage_key,
            collection_name=collection_name,
        )

    def _get_job(self, job_id: int) -> DeleteJobDO:
        return self.delete_job_dao.get_job(job_id)

    def _set_job_running(self, job_id: int) -> None:
        ok = self.delete_job_dao.set_job_running(job_id)
        if not ok:
            raise ValueError(f"Delete job {job_id} is terminal and cannot be started")

    def _transition_state(
        self,
        job_id: int,
        from_state: DeleteState,
        to_state: DeleteState,
        last_error: Optional[str] = None,
    ) -> None:
        if not from_state.can_transition_to(to_state):
            raise ValueError(f"Illegal state transition: {from_state.value} -> {to_state.value}")
        ok = self.delete_job_dao.transition_state(
            job_id=job_id,
            from_state=from_state,
            to_state=to_state,
            last_error=last_error,
        )
        if not ok:
            raise DeleteStateConflictError(
                f"Delete job {job_id} transition rejected due to stale or mismatched state"
            )

    def _set_job_failed(self, job_id: int, current_state: DeleteState, error_message: str) -> None:
        if current_state.is_terminal() or current_state == DeleteState.failed:
            return
        self._transition_state(
            job_id=job_id,
            from_state=current_state,
            to_state=DeleteState.failed,
            last_error=error_message[:2000],
        )

    def _compensate_document_status_on_fail(self, document_id: int, current_state: DeleteState) -> None:
        # If metadata is already deleted, keep document as deleted.
        # Otherwise avoid leaving it forever in `deleting`.
        if current_state in {DeleteState.pending, DeleteState.vector_deleted, DeleteState.failed}:
            self.lifecycle_dao.mark_delete_failed(document_id)

    def _complete_job(self, job_id: int, from_state: DeleteState) -> None:
        if not from_state.can_transition_to(DeleteState.completed):
            raise ValueError(f"Illegal completion transition: {from_state.value} -> {DeleteState.completed.value}")
        ok = self.delete_job_dao.complete_from_state(job_id=job_id, from_state=from_state)
        if not ok:
            raise DeleteStateConflictError(
                f"Delete job {job_id} completion rejected due to stale or mismatched state"
            )

    def _mark_document_deleting(self, document_id: int) -> None:
        self.lifecycle_dao.mark_deleting(document_id)

    def _delete_document_metadata(self, document_id: int) -> None:
        self.lifecycle_dao.mark_deleted_and_unbind(document_id)

    def _execute_from_state(self, job_row: DeleteJobDO) -> DeleteActionResultEntity:
        job_id = job_row.id
        document_id = job_row.document_id
        storage_key = job_row.storage_key
        collection_name = job_row.collection_name or "default"
        state = DeleteState(job_row.state)

        self._mark_document_deleting(document_id)

        if state.is_terminal():
            return DeleteActionResultEntity(
                ok=state == DeleteState.completed,
                job_id=job_id,
                document_id=document_id,
                state=state.value,
            )

        if state == DeleteState.pending:
            self.rag_dao.delete_doc(document_id=str(document_id), collection_name=collection_name)
            next_state = state.next_success_state()
            self._transition_state(job_id, from_state=state, to_state=next_state)
            state = next_state

        if state == DeleteState.vector_deleted:
            self._delete_document_metadata(document_id)
            next_state = state.next_success_state()
            self._transition_state(job_id, from_state=state, to_state=next_state)
            state = next_state

        if state == DeleteState.metadata_deleted:
            target = self._storage_abs(storage_key)
            if target.exists() and target.is_file():
                target.unlink()
            next_state = state.next_success_state()
            self._transition_state(job_id, from_state=state, to_state=next_state)
            state = next_state

        if state == DeleteState.file_deleted:
            self._complete_job(job_id, from_state=state)
            state = DeleteState.completed

        return DeleteActionResultEntity(ok=True, job_id=job_id, document_id=document_id, state=state.value)

    def delete_document(self, document_id: int, collection_name: str = "default") -> DeleteActionResultEntity:
        storage_key = self.lifecycle_dao.get_storage_key(document_id)

        job_id = self._create_or_get_job(
            document_id=document_id,
            storage_key=storage_key,
            collection_name=collection_name,
        )
        return self.retry_delete_job(job_id)

    def retry_delete_job(self, job_id: int) -> DeleteActionResultEntity:
        job_row = self._get_job(job_id)
        state = DeleteState(job_row.state)
        if state == DeleteState.completed:
            return DeleteActionResultEntity(
                ok=True,
                job_id=job_id,
                document_id=job_row.document_id,
                state=state.value,
            )
        try:
            if state == DeleteState.failed:
                # Redo semantics: rewind to pending and replay.
                self._transition_state(
                    job_id=job_id,
                    from_state=DeleteState.failed,
                    to_state=DeleteState.pending,
                    last_error=None,
                )

            self._set_job_running(job_id)
            latest = self._get_job(job_id)
            return self._execute_from_state(latest)
        except DeleteStateConflictError:
            # Another worker/request has advanced the state; do not mark failed.
            latest = self._get_job(job_id)
            return DeleteActionResultEntity(
                ok=True,
                job_id=job_id,
                document_id=latest.document_id,
                state=latest.state,
                note="state advanced by concurrent worker",
            )
        except Exception as exc:
            latest = self._get_job(job_id)
            latest_state = DeleteState(latest.state)
            self._set_job_failed(job_id, latest_state, str(exc))
            self._compensate_document_status_on_fail(latest.document_id, latest_state)
            return DeleteActionResultEntity(
                ok=False,
                job_id=job_id,
                document_id=job_row.document_id,
                state=DeleteState.failed.value,
                error=str(exc),
            )

    def get_delete_job(self, job_id: int) -> DeleteJobDO:
        return self.delete_job_dao.get_job(job_id)

    def close(self) -> None:
        self.rag_dao.close()
