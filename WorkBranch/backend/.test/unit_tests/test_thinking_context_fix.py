import ast
import datetime
import os
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXECUTOR_PATH = os.path.join(
    BACKEND_DIR,
    "service",
    "agent_service",
    "graph",
    "subgraphs",
    "tool_executor.py",
)


class _Console:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _Segment:
    def __init__(self, value):
        self.value = value


class RecordingLLM:
    def __init__(self):
        self.user_prompt = ""

    def chat_stream(self, messages, system_prompt, token_callback):
        self.user_prompt = messages[0]["content"]
        yield "thinking result"


def _load_thinking_executor():
    with open(EXECUTOR_PATH, encoding="utf-8") as source_file:
        tree = ast.parse(source_file.read(), filename=EXECUTOR_PATH)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_thinking_tool"
    )

    def build_context_prompt(parent_messages, conversation_messages, task):
        return f"{parent_messages}|{conversation_messages}|{task}"

    def build_special_prompt(
        task_description,
        previous_results,
        parent_chain_messages,
        current_conversation_messages,
        user_message="",
        settings_service=None,
    ):
        prompt = "|".join(map(str, [
            user_message,
            previous_results,
            parent_chain_messages,
            current_conversation_messages,
            task_description,
        ]))
        return "system", prompt

    namespace = {
        "Optional": object,
        "console": _Console(),
        "THINK_SYSTEM_PROMPT": "thinking-system",
        "datetime": datetime,
        "_build_child_agent_chat_prompt": build_special_prompt,
    }
    code = compile(ast.Module(body=[function], type_ignores=[]), EXECUTOR_PATH, "exec")
    exec(code, namespace)

    import builtins
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "service.agent_service.prompts.graph_prompts":
            return type("PromptModule", (), {"build_context_prompt": build_context_prompt})
        return original_import(name, globals, locals, fromlist, level)

    return namespace["_execute_thinking_tool"], fake_import


class ThinkingContextTest(unittest.TestCase):
    def test_thinking_receives_full_agent_context(self):
        import builtins

        execute_thinking, fake_import = _load_thinking_executor()
        llm = RecordingLLM()
        context = {
            "current_user_message_text": "ORIGINAL_USER_CONTEXT",
            "parent_chain_messages": [],
            "current_conversation_messages": [],
        }
        config = {
            "start_type": _Segment("thinking_start"),
            "delta_type": _Segment("thinking_delta"),
            "end_type": _Segment("thinking_end"),
        }
        tool_args = {
            "description": "CURRENT_THINKING_TASK",
            "previous_results": [{
                "tool_name": "read_file",
                "result": "PREVIOUS_TOOL_RESULT",
            }],
        }

        original_import = builtins.__import__
        builtins.__import__ = fake_import
        try:
            execute_thinking(
                "thinking",
                tool_args,
                "CURRENT_THINKING_TASK",
                llm,
                context,
                config,
            )
        finally:
            builtins.__import__ = original_import

        self.assertIn("ORIGINAL_USER_CONTEXT", llm.user_prompt)
        self.assertIn("PREVIOUS_TOOL_RESULT", llm.user_prompt)
        self.assertIn("CURRENT_THINKING_TASK", llm.user_prompt)


if __name__ == "__main__":
    unittest.main()
