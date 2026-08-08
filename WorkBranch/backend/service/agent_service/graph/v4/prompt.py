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


_SUBAGENT_TOOLS = {
    "call_explore_agent",
    "call_review_agent",
    "call_prediction_agent",
    "call_plan_agent",
}


def format_tool_records(tool_records: list[dict], max_rounds: int = 10) -> str:
    """<tool_records>：按 round 分组、批内按 call_seq 排序。"""
    if not tool_records:
        return "（暂无工具执行记录）"

    by_round: dict[Any, list[dict]] = {}
    for r in tool_records:
        if not isinstance(r, dict) or r.get("call_seq") is None:
            continue
        by_round.setdefault(r.get("round"), []).append(r)

    if not by_round:
        return "（暂无结构化工具执行记录）"

    rounds = sorted(by_round.keys())[-max_rounds:]
    lines: list[str] = []
    for rnd in rounds:
        items = sorted(by_round[rnd], key=lambda x: x.get("call_seq", 0))
        reason = next((i.get("reason") for i in items if i.get("reason")), "")
        header = f"round={rnd}"
        if reason:
            header += f' reason="{_clip(reason, 200)}"'
        lines.append(header)
        for item in items:
            status = item.get("status", "success")
            body = f"  call_seq={item.get('call_seq')} {item.get('tool_name')} status={status}"
            if status == "failed":
                body += f" error={_clip(item.get('error') or '', 500)}"
            else:
                result = item.get("result") or ""
                # 工具读取内容禁止裁剪（否则模型会误以为已读完而重复读/漏读）；
                # 仅子代理回传的生成文本允许裁剪，避免全文报告二次进上下文。
                if item.get("tool_name") in _SUBAGENT_TOOLS:
                    result = _clip(result, 3000)
                body += f" result={result}"
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
    from ...prompts.graph_prompts import build_tool_schema_prompt
    from ..subgraphs.tool_registry import get_allowed_tools

    allowed_tools = get_allowed_tools(agent_type, settings_service)
    tool_schema = build_tool_schema_prompt(allowed_tools, agent_type=agent_type)
    system_prompt = build_v4_system_prompt(tool_schema)
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
