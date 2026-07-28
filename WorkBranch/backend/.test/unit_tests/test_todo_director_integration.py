import os
import sys
import tempfile
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.graph.director_agent import (
    _director_post_execute_hook,
    build_initial_state,
    run_graph_v3,
)
from service.agent_service.prompts.graph_prompts import generate_prompt


class _TodoE2ELLM:
    def __init__(self):
        todos = ["inspect-e2e", "change-e2e", "verify-e2e"]
        self.responses = [
            {"kind": "tool", "tool_name": "update_todo", "tool_args": {"todos": todos, "doingIdx": 0}},
            {"kind": "tool", "tool_name": "update_todo", "tool_args": {"todos": todos, "doingIdx": 1}},
            {"kind": "tool", "tool_name": "update_todo", "tool_args": {"todos": todos, "doingIdx": 2}},
            {"kind": "tool", "tool_name": "chat", "tool_args": {"description": "report completion"}},
        ]
        self.decision_prompts = []

    def chat_with_json_mode(self, messages, **_kwargs):
        self.decision_prompts.append(messages[-1]["content"])
        assert self.responses, "unexpected extra decision"
        import json
        return json.dumps(self.responses.pop(0))

    def chat_stream(self, _messages, _system_prompt, stream_callback=None, **_kwargs):
        reply = "todo e2e complete"
        if stream_callback:
            stream_callback(reply)
        yield reply


class _ResumeE2ELLM:
    def __init__(self):
        self.decision_prompt = None

    def chat_with_json_mode(self, messages, **_kwargs):
        self.decision_prompt = messages[-1]["content"]
        return '{"kind":"tool","tool_name":"chat","tool_args":{"description":"resume"}}'

    def chat_stream(self, _messages, _system_prompt, stream_callback=None, **_kwargs):
        reply = "resumed"
        if stream_callback:
            stream_callback(reply)
        yield reply


class TodoDirectorIntegrationTest(unittest.TestCase):
    def test_full_graph_keeps_todo_across_react_iterations(self):
        llm = _TodoE2ELLM()
        previous_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as run_dir:
            try:
                os.chdir(run_dir)
                final_state = run_graph_v3(
                    {"role": "user", "content": "execute the three todo steps"},
                    "workspace-e2e",
                    llm_service=llm,
                )
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(final_state["todos"], ["inspect-e2e", "change-e2e", "verify-e2e"])
        self.assertEqual(final_state["current_todo_index"], 2)
        self.assertEqual(final_state["final_reply"], "todo e2e complete")
        self.assertEqual(len(llm.decision_prompts), 4)
        for prompt in llm.decision_prompts[1:]:
            self.assertIn("inspect-e2e", prompt)
            self.assertIn("change-e2e", prompt)
            self.assertIn("verify-e2e", prompt)

        resume_llm = _ResumeE2ELLM()
        with tempfile.TemporaryDirectory() as run_dir:
            try:
                os.chdir(run_dir)
                resumed_state = run_graph_v3(
                    {"role": "user", "content": "continue in this conversation"},
                    "workspace-e2e",
                    llm_service=resume_llm,
                    prior_agent_state=final_state,
                )
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(resumed_state["todos"], final_state["todos"])
        self.assertEqual(resumed_state["current_todo_index"], 2)
        self.assertIsNotNone(resume_llm.decision_prompt)
        for todo in final_state["todos"]:
            self.assertIn(todo, resume_llm.decision_prompt)

    def test_hook_updates_agent_state_when_subgraph_result_has_no_todo_fields(self):
        todos = ["inspect", "change", "verify"]
        state = {
            "pending_tools": [{
                "tool_name": "update_todo",
                "args": {"todos": todos, "doingIdx": 1},
            }],
        }
        direct_update = {"last_tool_name": "update_todo", "last_tool_success": True}
        filtered_subgraph_result = {"result": "audit written", "error": None}

        update = _director_post_execute_hook(direct_update, filtered_subgraph_result, state)

        self.assertEqual(update["todos"], todos)
        self.assertEqual(update["current_todo_index"], 1)

    def test_same_conversation_restores_todo_and_new_conversation_is_empty(self):
        prior_state = {
            "workspace_id": "workspace-a",
            "todos": ["inspect", "change", "verify"],
            "current_todo_index": 2,
        }

        resumed = build_initial_state(
            {"role": "user", "content": "continue"},
            "workspace-a",
            prior_agent_state=prior_state,
        )
        fresh = build_initial_state(
            {"role": "user", "content": "new task"},
            "workspace-b",
        )

        self.assertEqual(resumed["todos"], prior_state["todos"])
        self.assertEqual(resumed["current_todo_index"], 2)
        self.assertEqual(fresh["todos"], [])
        self.assertEqual(fresh["current_todo_index"], 0)

    def test_prompt_reads_todo_from_agent_state(self):
        todos = ["inspect-unique", "change-unique", "verify-unique"]

        _system_prompt, context_prompt = generate_prompt(
            agent_type="director_agent",
            mode="DIRECT",
            user_message="continue",
            workspace_id="workspace-a",
            iteration_count=2,
            max_iterations=10,
            tool_schema_prompt="",
            tool_history=[],
            last_tool_result=None,
            todos=todos,
            current_todo_index=2,
        )

        for todo in todos:
            self.assertIn(todo, context_prompt)
        self.assertLess(context_prompt.index(todos[0]), context_prompt.index(todos[1]))
        self.assertLess(context_prompt.index(todos[1]), context_prompt.index(todos[2]))


if __name__ == "__main__":
    unittest.main()
