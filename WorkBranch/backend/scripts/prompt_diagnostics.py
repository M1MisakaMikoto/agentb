"""
提示词诊断 CLI（参考 hermes-agent 的 prompt-size 诊断实践）

用法（在 backend 目录或容器内执行）：
    python scripts/prompt_diagnostics.py                  # 分层尺寸 + stable 稳定性
    python scripts/prompt_diagnostics.py --parse-replay f.jsonl
                                                          # 回放原始响应到统一解析器

--parse-replay 输入格式：每行一个 JSON 字符串（原始 LLM 响应文本）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)


def _sample_prompt_args() -> dict:
    return {
        "agent_type": "director_agent",
        "mode": "DIRECT",
        "user_message": "读取目标文件并汇总结果",
        "workspace_id": "workspace-diag",
        "iteration_count": 1,
        "max_iterations": 10,
        "tool_schema_prompt": "工具列表：\nread_file:{\"file_path\":\"(文件路径)\"}",
        "tool_history": [
            {"tool_name": "read_file", "args": {"file_path": "/tmp/a.txt"},
             "result": "内容示例" * 100, "timestamp": "2026-08-06T00:00:00"},
        ],
        "last_tool_result": "内容示例",
        "todos": ["第一步：读取", "第二步：汇总"],
        "current_todo_index": 0,
        "parent_chain_messages": [
            {"role": "user", "content": "上一条历史消息"},
            {"role": "assistant", "content": "上一条回复"},
        ],
        "current_conversation_messages": [
            {"role": "user", "content": "当前会话里的问题"},
        ],
    }


def cmd_sizes() -> int:
    from service.agent_service.prompts.builder.prompt_builder import PromptBuilder

    builder = PromptBuilder()
    kwargs = _sample_prompt_args()

    sys_p, user_p, stats = builder.generate_prompt(**kwargs)
    print("=== Prompt 分层尺寸 ===")
    print(stats.describe())
    print()
    print(f"stable 层内容 ({stats.stable_chars} chars):")
    print(sys_p)
    print()
    print(f"user message 前 500 chars:")
    print(user_p[:500])
    print()

    # stable 稳定性验证：相同输入两次构建，hash 必须一致
    sys_p2, _user2, stats2 = builder.generate_prompt(**kwargs)
    stable_stable = stats.stable_hash == stats2.stable_hash and sys_p == sys_p2
    print(f"stable 层稳定性: {'PASS (hash 一致)' if stable_stable else 'FAIL (hash 不一致!)'}")

    # 修改 volatile 输入（轮次/历史），stable 必须仍一致
    kwargs["iteration_count"] = 5
    kwargs["tool_history"] = []
    _sys_p3, _user3, stats3 = builder.generate_prompt(**kwargs)
    print(
        "volatile 变化后 stable 稳定性: "
        f"{'PASS' if stats.stable_hash == stats3.stable_hash else 'FAIL'}"
    )
    return 0 if stable_stable else 1


def cmd_parse_replay(path: str) -> int:
    from service.agent_service.graph.decision.tool_call_parser import (
        DecisionParseError,
        parse_tool_decision_response,
    )

    total = 0
    ok = 0
    failures: list[tuple[int, str, str]] = []

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                raw = line
            total += 1
            try:
                parse_tool_decision_response(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
                ok += 1
            except DecisionParseError as e:
                failures.append((line_no, e.category, str(e)[:200]))

    print(f"=== 解析回放: {path} ===")
    print(f"共 {total} 条，成功 {ok}，失败 {len(failures)}")
    for line_no, category, msg in failures[:20]:
        print(f"  L{line_no} [{category}] {msg}")
    if len(failures) > 20:
        print(f"  ... 其余 {len(failures) - 20} 条省略")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt 诊断工具")
    parser.add_argument("--parse-replay", metavar="FILE", help="回放原始 LLM 响应到统一解析器")
    args = parser.parse_args()

    if args.parse_replay:
        return cmd_parse_replay(args.parse_replay)
    return cmd_sizes()


if __name__ == "__main__":
    sys.exit(main())
