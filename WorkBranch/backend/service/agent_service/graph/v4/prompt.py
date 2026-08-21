"""V4 标签化提示词装配（定稿标签规范）。

区块顺序（stable -> context -> volatile）：
    <system> 身份 / 工具协议 / 输出契约（跨轮不变）
    <current_task> 协议硬任务（acting 出错后改写为总结错误）
    <context> 历史对话（压缩）
    <todos> / <plan>
    <tool_records> 结构化工具记录（round + call_seq + reason）
    <parse_error> 解析错误固定字段（含原文）
    <closur-feedback> closuring 注入的提示性反馈
    <user_question> 当前用户问题
"""

from __future__ import annotations

import json
from typing import Any, Optional


V4_SYSTEM_PROMPT = """你是一个任务执行代理（leader）。你的职责是根据 <current_task>、<context>、<tool_records> 等信息决定下一步动作，并严格按输出协议输出。

## 输出协议（必须遵守）
每一轮只能输出以下三种 JSON 之一，不得输出任何标签、解释或额外文本：
1. 调用工具（支持 1..N 个并行调用）：
   {"type":"tool_calls","content":{"reason":"这批调用的目的","calls":[
     {"call_seq":1,"tool_name":"工具名","tool_args":{"参数":"值"},"task_description":"原因"},
     {"call_seq":2,"tool_name":"工具名","tool_args":{}}
   ]}}
2. 向用户输出最终总结：
   {"type":"text","content":"最终总结文本"}
3. 无文本的完成：
   {"type":"done","content":null}

## 规则
1. call_seq 在批次内唯一（1..N），tool_name 必须来自工具协议，tool_args 严格使用协议参数名
2. 一次输出一批互不依赖的工具调用并行执行；有依赖关系的调用必须在后续轮次中发起
3. 只有确实完成全部工作且能输出最终总结时，才允许 type=text；text 之后即结束
4. 无法继续时必须用 type=text 说明阻塞原因与已确认结果，不得输出空 done
5. 不要输出标签（<system> 等仅用于区分输入内容）

{tool_prompt}"""


V4_DIRECTOR_EXECUTION_PROMPT = """## Director 执行规则
1. 禁止调用 thinking 或任何 call_*_agent 子代理工具；直接使用业务工具完成任务。
2. 目录列表和工作区新文件信息会提供 size。根据文件大小、任务目标和上下文容量自行判断是否属于大文件。
3. 遇到大文件，优先使用搜索类工具定位内容。DOC/DOCX/PDF/XLS/XLSX 优先用 document 的 s 操作，构造覆盖点位编号、目标字段、表头及常见同义词的正则，争取一次取得足够多的相关条目；不要从头到尾分段通读。
4. document s 像 grep 一样按命中行返回，行内命中在 occurrences 中；结果包含片段、字符偏移、段号和 read_hint。需要下一页时把 next_start_idx 传给 s 的 start_idx；只有片段缺少所需上下文时，才把 read_hint 原样用于 r 定点读取；不要重复读取同一范围。
5. 根据用户要求判断信息是否足够，不以命中数、返回数或文本长度作为依据。若缺失信息会影响结论则继续查，否则立即推进工作。"""


_CURRENT_TASK_DEFAULT = (
    "请严格按输出协议输出：type 属于 {tool_calls, text, done}；"
    "tool_calls 的 content 必须是 {reason, calls[]}，calls 数组 1..N，call_seq 唯一，"
    "tool_name 必须来自协议内的工具名，tool_args 使用协议内参数名；"
    "text 的 content 为最终总结文本；done 的 content 为 null 或字符串。"
)


def build_v4_system_prompt(tool_schema_prompt: str = "") -> str:
    """stable 层：身份 + 输出契约 + 工具协议。"""
    # 注意：模板内含字面 JSON 花括号，不能使用 str.format，只能 replace
    return V4_SYSTEM_PROMPT.replace("{tool_prompt}", tool_schema_prompt)


def build_current_task(*, acting_failures: Optional[list[dict]] = None) -> str:
    """
    <current_task> 双态：
    - 平时：输出协议的硬任务（格式/内容类型层面，不涉及业务）；
    - 本轮有工具失败：改写为总结本轮错误并决定下一步。
    """
    if acting_failures:
        failed = [f for f in acting_failures if f.get("status") == "failed"]
        if failed:
            names = ", ".join(
                f"{f.get('call_seq')}:{f.get('tool_name')}" for f in failed[:8]
            )
            return (
                f"本轮有 {len(failed)} 个工具调用失败（见 <tool_records> 中 status=failed 记录，"
                f"涉及 {names}）。请总结失败原因与已成功结果，并决定下一步："
                f"补调用 / 换工具 / 输出 text 结束。"
            )
    return _CURRENT_TASK_DEFAULT


def _clip(text: Any, limit: int = 3000) -> str:
    s = str(text if text is not None else "")
    if len(s) <= limit:
        return s
    return f"{s[:1500]}\n[中间省略 {len(s) - limit} 字符]\n{s[-1500:]}"


def _format_hermes_document_read_result(result: dict) -> str:
    """Render a Pandoc read in Hermes read_file's model-facing style."""
    required = {
        "content",
        "total_lines",
        "start_line",
        "end_line",
        "file_size",
        "read_range",
        "truncated",
    }
    missing = required.difference(result)
    assert not missing, f"Pandoc document read missing Hermes fields: {sorted(missing)}"

    content = str(result["content"] or "")
    start_line = int(result["start_line"])
    numbered_content = "\n".join(
        f"{start_line + index}|{line}"
        for index, line in enumerate(content.splitlines())
    )
    payload = {
        "content": numbered_content,
        "total_lines": int(result["total_lines"]),
        "file_size": int(result["file_size"]),
        "truncated": bool(result["truncated"]),
        "extracted_document": True,
    }
    if payload["truncated"]:
        next_start_idx = result.get("next_start_idx")
        assert next_start_idx is not None, "truncated Pandoc read missing next_start_idx"
        payload["hint"] = (
            f"Use start_idx={next_start_idx} to continue reading "
            f"(showing lines {result['start_line']}-{result['end_line']} "
            f"of {result['total_lines']} lines; character range "
            f"{result['read_range']} of {result['total_length']})"
        )
    payload["read_range"] = str(result["read_range"])
    if payload["truncated"]:
        payload["next_start_idx"] = int(result["next_start_idx"])
    return json.dumps(payload, ensure_ascii=False)


def _model_facing_tool_result(item: dict, result: Any) -> str:
    metadata = result.get("metadata") if isinstance(result, dict) else None
    if (
        item.get("tool_name") == "document"
        and (item.get("args") or {}).get("operation") == "r"
        and isinstance(metadata, dict)
        and metadata.get("method") == "pandoc"
    ):
        return _format_hermes_document_read_result(result)
    return str(result)


_SUBAGENT_TOOLS = {
    "call_explore_agent",
    "call_review_agent",
    "call_prediction_agent",
    "call_plan_agent",
}


def build_agent_tool_schema(agent_type: str, settings_service=None) -> str:
    """Build the exact tool description exposed to an agent type."""
    from ...prompts.graph_prompts import build_tool_schema_prompt
    from ..subgraphs.tool_registry import get_allowed_tools

    allowed_tools = get_allowed_tools(agent_type, settings_service)
    if agent_type == "director_agent":
        disabled_tools = {"thinking", *_SUBAGENT_TOOLS}
        allowed_tools = [name for name in allowed_tools if name not in disabled_tools]
    return build_tool_schema_prompt(allowed_tools, agent_type=agent_type)


def format_tool_records(tool_records: list[dict], max_rounds: int = 10) -> str:
    """<tool_records>：按 round 分组、批内按 call_seq 排序。"""
    if not tool_records:
        return "（暂无工具执行记录）"

    by_round: dict[Any, list[dict]] = {}
    reasons: dict[Any, str] = {}
    for r in tool_records:
        if not isinstance(r, dict):
            continue
        if r.get("call_seq") is None:
            if r.get("round") is not None and r.get("reason"):
                reasons[r.get("round")] = str(r.get("reason"))
            continue
        by_round.setdefault(r.get("round"), []).append(r)

    if not by_round:
        return "（暂无结构化工具执行记录）"

    rounds = sorted(by_round.keys())[-max_rounds:]
    lines: list[str] = []
    for rnd in rounds:
        items = sorted(by_round[rnd], key=lambda x: x.get("call_seq", 0))
        reason = reasons.get(rnd) or next(
            (i.get("reason") for i in items if i.get("reason")), ""
        )
        header = f"round={rnd}"
        if reason:
            header += f' reason="{_clip(reason, 200)}"'
        lines.append(header)
        for item in items:
            status = item.get("status", "success")
            body = f"  call_seq={item.get('call_seq')} {item.get('tool_name')} status={status}"
            request = json.dumps(
                item.get("args") or {}, ensure_ascii=False, sort_keys=True, default=str
            )
            body += f" request={request}"
            if item.get("task_description"):
                task_description = json.dumps(
                    str(item.get("task_description")), ensure_ascii=False
                )
                body += f" task_description={task_description}"
            if status == "failed":
                body += f" error={_clip(item.get('error') or '', 500)}"
            else:
                result = item.get("result") or ""
                # 工具读取内容禁止裁剪（否则模型会误以为已读完而重复读/漏读）；
                # 仅子代理回传的生成文本允许裁剪，避免全文报告二次进上下文。
                if item.get("tool_name") in _SUBAGENT_TOOLS:
                    result = _clip(result, 3000)
                body += f" result={_model_facing_tool_result(item, result)}"
            if item.get("duration_ms") is not None:
                body += f" duration_ms={item.get('duration_ms')}"
            lines.append(body)
    return "\n".join(lines)


def format_context_section(
    parent_chain_messages: Optional[list[dict]],
    current_conversation_messages: Optional[list[dict]],
    message_context: Optional[dict] = None,
) -> str:
    from ...prompts.base.message_processor import MessageProcessor

    processor = MessageProcessor(
        (message_context or {}).get("settings_service") if message_context else None
    )
    parts: list[str] = []
    if parent_chain_messages:
        p = processor.process_conversation_context(
            messages=parent_chain_messages,
            source_type="parent_chain",
            message_context=message_context,
        )
        if p:
            parts.append(p)
    if current_conversation_messages:
        c = processor.process_conversation_context(
            messages=current_conversation_messages,
            source_type="current_conversation",
            message_context=message_context,
        )
        if c:
            parts.append(c)
    return "\n".join(parts)


def format_todo_block(todos: list[str], current_todo_index: int) -> str:
    if not todos:
        return ""
    lines = ["当前 TODO 列表："]
    for idx, todo in enumerate(todos):
        marker = " <= 当前执行项" if idx == current_todo_index else ""
        lines.append(f"- [{idx}] {todo}{marker}")
    lines.append(f"doingIdx={current_todo_index}")
    return "\n".join(lines)


def build_tagged_prompt(
    *,
    agent_type: str,
    user_message: str,
    workspace_id: str,
    round_no: int,
    max_iterations: int,
    tool_records: list[dict],
    todos: list[str],
    current_todo_index: int,
    plan_content: Optional[str],
    parent_chain_messages: list[dict],
    current_conversation_messages: list[dict],
    parse_error: Optional[str] = None,
    closur_feedback: Optional[str] = None,
    acting_failures: Optional[list[dict]] = None,
    settings_service=None,
    message_context: Optional[dict] = None,
    system_prompt_override: Optional[str] = None,
) -> tuple[str, str]:
    """组装 V4 标签化提示词，返回 (system_prompt, user_message)。"""
    tool_schema = build_agent_tool_schema(agent_type, settings_service)
    system_prompt = build_v4_system_prompt(tool_schema)
    if agent_type == "director_agent":
        system_prompt = system_prompt + "\n\n" + V4_DIRECTOR_EXECUTION_PROMPT
    if system_prompt_override:
        system_prompt = system_prompt + "\n\n" + system_prompt_override

    current_task = build_current_task(acting_failures=acting_failures)
    context = format_context_section(
        parent_chain_messages,
        current_conversation_messages,
        message_context,
    )
    todo_block = format_todo_block(todos, current_todo_index)
    plan_block = (
        f"当前工作区存在计划文件 plan.md：{_clip(plan_content, 2000)}"
        if plan_content
        else ""
    )
    records = format_tool_records(tool_records)

    sections = [f"<system>\n{system_prompt}\n</system>"]
    sections.append(f"<current_task>\n{current_task}\n</current_task>")
    if context:
        sections.append(f"<context>\n{context}\n</context>")
    if todo_block:
        sections.append(f"<todos>\n{todo_block}\n</todos>")
    if plan_block:
        sections.append(f"<plan>\n{plan_block}\n</plan>")
    sections.append(f"<tool_records>\n{records}\n</tool_records>")
    if parse_error:
        sections.append(f"<parse_error>\n{parse_error}\n</parse_error>")
    if closur_feedback:
        sections.append(f"<closur-feedback>\n{closur_feedback}\n</closur-feedback>")
    sections.append(f"<user_question>\n{user_message}\n</user_question>")

    # meta 放末尾：轮次数字若在 user 前缀会破坏每轮前缀缓存命中
    # （system/current_task/context 前半段跨轮稳定，应可被前缀缓存覆盖）
    meta = (
        f"当前工作区ID: {workspace_id} | 轮次: {round_no}/{max_iterations} "
        f"| agent_type: {agent_type}"
    )
    return system_prompt, "\n\n".join([*sections, meta])


# ---------- 固定终止模板（异常路径，跳过 closuring） ----------


def fixed_parse_failure_text(detail: str, raw_text: str) -> str:
    """解析重试超限：固定模板 + 错误信息 + 出问题的解析原文（调试用）。"""
    raw_clip = _clip(raw_text, 2000)
    return (
        f"解析连续失败，已终止。\n错误信息: {detail}\n"
        f"原始输出（调试用）: {raw_clip}"
    )


def fixed_iteration_limit_text(max_iterations: int, recent_results: list[str]) -> str:
    summary = "；".join(_clip(r, 300) for r in recent_results[-3:] if r) or "无"
    return f"已达最大轮次 {max_iterations}，任务未完成。当前已确认进展: {summary}"


def fixed_tool_loop_text(tool_name: str, repeat: int, recent_results: list[str]) -> str:
    summary = "；".join(_clip(r, 300) for r in recent_results[-3:] if r) or "无"
    return (
        f"检测到工具连续失败循环（{tool_name} 连续失败 {repeat} 次），已终止。"
        f"当前已确认进展: {summary}"
    )
