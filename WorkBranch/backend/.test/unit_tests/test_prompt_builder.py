import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.prompts.builder.prompt_builder import PromptBuilder
from service.agent_service.prompts.graph_prompts import generate_prompt


def _prompt_args(**updates):
    args = {
        "agent_type": "director_agent",
        "mode": "DIRECT",
        "user_message": "读取目标文件并汇总",
        "workspace_id": "ws-test",
        "iteration_count": 1,
        "max_iterations": 10,
        "tool_schema_prompt": '工具列表：\nread_file:{"file_path":"(文件路径)"}',
        "tool_history": [
            {
                "tool_name": "read_file",
                "args": {"file_path": "/tmp/a.txt"},
                "result": "文件内容示例",
                "timestamp": "2026-08-06T00:00:00",
            }
        ],
        "last_tool_result": "文件内容示例",
        "todos": ["读取文件", "汇总结果"],
        "current_todo_index": 0,
        "parent_chain_messages": [
            {"role": "user", "content": "历史问题"},
            {"role": "assistant", "content": "历史回复"},
        ],
        "current_conversation_messages": [
            {"role": "user", "content": "当前问题"},
        ],
    }
    args.update(updates)
    return args


def _legacy_expected_user_message(args) -> str:
    """按旧 generate_prompt 的装配逻辑复现期望输出，作为结构拆分的回归基线。"""
    from service.agent_service.prompts.base.message_processor import MessageProcessor
    from service.agent_service.prompts.templates.user_templates import UserTemplateManager
    from service.agent_service.prompts.graph_prompts import _format_tool_history, format_current_question

    processor = MessageProcessor()
    static_section = UserTemplateManager.build_static_section()
    dynamic_section = processor.build_dynamic_section(
        user_message=args["user_message"],
        workspace_id=args["workspace_id"],
        todos=args["todos"],
        current_todo_index=args["current_todo_index"],
        plan_content=args.get("plan_content"),
        include_iteration=False,
    )

    conversation_context = ""
    if args.get("parent_chain_messages"):
        parent_context = processor.process_conversation_context(
            messages=args["parent_chain_messages"],
            source_type="parent_chain",
        )
        if parent_context:
            conversation_context += parent_context + "\n"
    if args.get("current_conversation_messages"):
        current_context = processor.process_conversation_context(
            messages=args["current_conversation_messages"],
            source_type="current_conversation",
        )
        if current_context:
            conversation_context += current_context

    if args["mode"].upper() == "PLAN":
        dynamic_section += "\n\n" + UserTemplateManager.build_plan_mode_suffix()

    tool_history_section = ""
    history_block = _format_tool_history(args["tool_history"])
    if args["tool_history"]:
        tool_history_section = f"\n\n{history_block}\n"

    user_message_text = processor.build_full_user_message(
        static_content=static_section,
        dynamic_content=dynamic_section,
        conversation_context=conversation_context.strip(),
    )
    if tool_history_section:
        user_message_text += tool_history_section
    if args["user_message"]:
        user_message_text += format_current_question(args["user_message"])
    if args.get("last_error"):
        from service.agent_service.prompts.error_injection import format_error_for_prompt
        user_message_text += "\n\n" + format_error_for_prompt(args["last_error"])
    return user_message_text


class PromptBuilderTest(unittest.TestCase):
    def test_legacy_entry_matches_builder_output(self):
        """generate_prompt（旧入口）与 PromptBuilder 输出必须逐字节一致。"""
        args = _prompt_args()
        sys1, user1 = generate_prompt(**args)
        builder = PromptBuilder()
        sys2, user2, stats = builder.generate_prompt(**args)

        self.assertEqual(sys1, sys2)
        self.assertEqual(user1, user2)
        self.assertGreater(stats.total_chars, 0)

    def test_user_message_matches_legacy_assembly(self):
        """结构拆分后的 user message 与旧装配逻辑逐字节一致。"""
        args = _prompt_args()
        _sys, user = generate_prompt(**args)
        self.assertEqual(user, _legacy_expected_user_message(args))

    def test_stable_layer_stable_across_volatile_changes(self):
        """stable 层跨轮 hash 不变；volatile 变化不影响 stable。"""
        builder = PromptBuilder()
        base_args = _prompt_args()
        _s1, _u1, stats1 = builder.generate_prompt(**base_args)

        changed_args = _prompt_args(
            iteration_count=5,
            tool_history=[],
            user_message="另一个问题",
            current_conversation_messages=[],
        )
        _s2, _u2, stats2 = builder.generate_prompt(**changed_args)

        self.assertEqual(stats1.stable_hash, stats2.stable_hash)
        self.assertEqual(stats1.stable_chars, stats2.stable_chars)
        self.assertNotEqual(stats1.user_message_chars, stats2.user_message_chars)

    def test_plan_mode_suffix_included(self):
        args = _prompt_args(mode="PLAN")
        _sys, user = generate_prompt(**args)
        self.assertIn("kind=step_done", user)

    def test_stats_describe(self):
        builder = PromptBuilder()
        args = _prompt_args()
        _s, _u, stats = builder.generate_prompt(**args)
        self.assertIn("stable=", stats.describe())
        self.assertIn("total=", stats.describe())


if __name__ == "__main__":
    unittest.main()
