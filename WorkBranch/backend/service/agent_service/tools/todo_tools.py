"""
TODO 工具 - 单一 update_todo
"""
from typing import List
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
        self.state = TodoState()
        self._load()

    def _load(self):
        if os.path.exists(self.todo_file):
            try:
                with open(self.todo_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state = TodoState(**data)
            except Exception as e:
                print(f"[Todo] 加载任务列表失败: {e}")
                self.state = TodoState()

    def _save(self):
        os.makedirs(os.path.dirname(self.todo_file), exist_ok=True)
        try:
            with open(self.todo_file, "w", encoding="utf-8") as f:
                json.dump(self.state.model_dump(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Todo] 保存任务列表失败: {e}")

    def update(self, todos: List[str], doing_idx: int) -> TodoState:
        normalized_todos = [str(item).strip() for item in (todos or []) if str(item).strip()]
        max_index = max(len(normalized_todos) - 1, 0)
        normalized_doing_idx = 0 if not normalized_todos else min(max(int(doing_idx), 0), max_index)
        self.state = TodoState(todos=normalized_todos, doingIdx=normalized_doing_idx)
        self._save()
        return self.state

    def get_state(self) -> TodoState:
        return self.state



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
