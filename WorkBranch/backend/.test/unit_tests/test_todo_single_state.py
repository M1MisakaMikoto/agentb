import json
import importlib.util
import os
import tempfile
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TODO_TOOLS_PATH = os.path.join(BACKEND_DIR, "service", "agent_service", "tools", "todo_tools.py")
TODO_TOOLS_SPEC = importlib.util.spec_from_file_location("todo_tools_under_test", TODO_TOOLS_PATH)
assert TODO_TOOLS_SPEC and TODO_TOOLS_SPEC.loader
TODO_TOOLS = importlib.util.module_from_spec(TODO_TOOLS_SPEC)
TODO_TOOLS_SPEC.loader.exec_module(TODO_TOOLS)
TodoList = TODO_TOOLS.TodoList
build_todo_agent_state_update = TODO_TOOLS.build_todo_agent_state_update
restore_todo_checkpoint = TODO_TOOLS.restore_todo_checkpoint


class TodoSingleStateTest(unittest.TestCase):
    def test_todo_file_is_write_only_audit_mirror(self):
        with tempfile.TemporaryDirectory() as base_dir:
            workspace_id = "workspace-a"
            workspace_dir = os.path.join(base_dir, workspace_id)
            os.makedirs(workspace_dir)
            todo_file = os.path.join(workspace_dir, "todo.json")
            with open(todo_file, "w", encoding="utf-8") as stream:
                json.dump({"todos": ["stale"], "doingIdx": 0}, stream)

            todo = TodoList(workspace_id, base_dir=base_dir)
            self.assertFalse(hasattr(todo, "state"))
            state = todo.update(["first", "second"], 1)

            self.assertEqual(state.todos, ["first", "second"])
            self.assertEqual(state.doingIdx, 1)
            with open(todo_file, "r", encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), {"todos": ["first", "second"], "doingIdx": 1})

    def test_agent_state_advances_without_tool_subgraph_state(self):
        todos = ["inspect", "change", "verify"]

        updates = [build_todo_agent_state_update(todos, index) for index in range(3)]

        self.assertEqual([item["current_todo_index"] for item in updates], [0, 1, 2])
        self.assertTrue(all(item["todos"] == todos for item in updates))

    def test_checkpoint_is_scoped_to_conversation_workspace(self):
        checkpoint = {
            "workspace_id": "workspace-a",
            "todos": ["inspect", "change", "verify"],
            "current_todo_index": 2,
        }

        restored = restore_todo_checkpoint(checkpoint, "workspace-a")
        new_conversation = restore_todo_checkpoint(None, "workspace-a")

        self.assertEqual(restored.todos, checkpoint["todos"])
        self.assertEqual(restored.doingIdx, 2)
        self.assertEqual(new_conversation.todos, [])
        self.assertEqual(new_conversation.doingIdx, 0)
        with self.assertRaisesRegex(AssertionError, "workspace mismatch"):
            restore_todo_checkpoint(checkpoint, "workspace-b")

    def test_checkpoint_rejects_drifted_state(self):
        with self.assertRaisesRegex(AssertionError, "non-normalized"):
            restore_todo_checkpoint(
                {"workspace_id": "workspace-a", "todos": [" inspect "], "current_todo_index": 0},
                "workspace-a",
            )
        with self.assertRaisesRegex(AssertionError, "out of bounds"):
            restore_todo_checkpoint(
                {"workspace_id": "workspace-a", "todos": ["inspect"], "current_todo_index": 2},
                "workspace-a",
            )


if __name__ == "__main__":
    unittest.main()
