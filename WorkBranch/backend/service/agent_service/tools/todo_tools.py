"""
TODO 工具 - 单一 update_todo
"""
from typing import Any, List, Optional
from pydantic import BaseModel, Field
import json
import os


class TodoState(BaseModel):
    """TODO 真值"""
    todos: List[str] = Field(default_factory=list, description="完整有序待办列表")
    doingIdx: int = Field(default=0, description="当前正在执行的待办下标，从 0 开始")


class TodoList:
    """TODO 状态管理器"""

    def __init__(self, workspace_id: str, base_dir: str = "workspaces"):
        self.workspace_id = workspace_id
        self.base_dir = base_dir
        self.todo_file = os.path.join(base_dir, workspace_id, "todo.json")

    def _save(self, state: TodoState):
        os.makedirs(os.path.dirname(self.todo_file), exist_ok=True)
        with open(self.todo_file, "w", encoding="utf-8") as f:
            json.dump(state.model_dump(), f, ensure_ascii=False, indent=2)

    def update(self, todos: List[str], doing_idx: int) -> TodoState:
        state = normalize_todo_state(todos, doing_idx)
        self._save(state)
        return state


def normalize_todo_state(todos: List[str], doing_idx: int) -> TodoState:
    normalized_todos = [str(item).strip() for item in (todos or []) if str(item).strip()]
    max_index = max(len(normalized_todos) - 1, 0)
    normalized_doing_idx = 0 if not normalized_todos else min(max(int(doing_idx), 0), max_index)
    return TodoState(todos=normalized_todos, doingIdx=normalized_doing_idx)


def build_todo_agent_state_update(todos: List[str], doing_idx: int) -> dict:
    state = normalize_todo_state(todos, doing_idx)
    return {
        "todos": state.todos,
        "current_todo_index": state.doingIdx,
        "current_todo_goal": None,
        "current_todo_done_when": None,
        "iteration_count": 0,
        "current_todo_iteration_count": 0,
        "todo_status": "pending",
    }


def restore_todo_checkpoint(prior_agent_state: Optional[dict], workspace_id: str) -> TodoState:
    if not prior_agent_state or "todos" not in prior_agent_state:
        return normalize_todo_state([], 0)

    assert prior_agent_state.get("workspace_id") == workspace_id, "TODO checkpoint workspace mismatch"
    checkpoint_todos: List[Any] = prior_agent_state.get("todos") or []
    checkpoint_index = prior_agent_state.get("current_todo_index", 0) or 0
    assert all(isinstance(item, str) for item in checkpoint_todos), "TODO checkpoint must contain strings"
    state = normalize_todo_state(checkpoint_todos, checkpoint_index)
    assert state.todos == checkpoint_todos, "TODO checkpoint contains non-normalized items"
    assert state.doingIdx == checkpoint_index, "TODO checkpoint index is out of bounds"
    return state



def update_todo(workspace_id: str, todos: List[str], doingIdx: int) -> dict:
    todo = TodoList(workspace_id)
    state = todo.update(todos=todos, doing_idx=doingIdx)
    current = state.todos[state.doingIdx] if state.todos else None
    return {
        "result": json.dumps(state.model_dump(), ensure_ascii=False),
        "error": None,
        "todos": state.todos,
        "doingIdx": state.doingIdx,
        "current": current,
    }


TOOL_DEFINITIONS = [
    {
        "name": "update_todo",
        "description": "用完整列表覆盖更新 TODO 状态",
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "完整有序待办列表"
                },
                "doingIdx": {
                    "type": "integer",
                    "description": "当前正在执行的待办下标，从 0 开始"
                }
            },
            "required": ["todos", "doingIdx"]
        }
    }
]
