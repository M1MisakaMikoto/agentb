from __future__ import annotations

from enum import Enum


class DeleteState(str, Enum):
    pending = "pending"
    vector_deleted = "vector_deleted"
    metadata_deleted = "metadata_deleted"
    file_deleted = "file_deleted"
    completed = "completed"
    failed = "failed"

    def is_terminal(self) -> bool:
        return self in {DeleteState.completed}

    def can_transition_to(self, target: "DeleteState") -> bool:
        transitions = {
            DeleteState.pending: {DeleteState.vector_deleted, DeleteState.failed},
            DeleteState.vector_deleted: {DeleteState.metadata_deleted, DeleteState.failed},
            DeleteState.metadata_deleted: {DeleteState.file_deleted, DeleteState.failed},
            DeleteState.file_deleted: {DeleteState.completed, DeleteState.failed},
            DeleteState.completed: set(),
            DeleteState.failed: {DeleteState.pending},
        }
        return target in transitions[self]

    def next_success_state(self) -> "DeleteState":
        next_map = {
            DeleteState.pending: DeleteState.vector_deleted,
            DeleteState.vector_deleted: DeleteState.metadata_deleted,
            DeleteState.metadata_deleted: DeleteState.file_deleted,
            DeleteState.file_deleted: DeleteState.completed,
        }
        if self not in next_map:
            raise ValueError(f"No forward success transition from terminal state: {self.value}")
        return next_map[self]
