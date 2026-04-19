from typing import Any, List, Optional

from service.session_service.message_content import (
    build_prompt_safe_text,
    build_user_message,
    resolve_runtime_parts,
)
from service.agent_service.graph.subgraphs.tool_registry import generate_tool_prompt
from singleton import get_workspace_service


workspace_service = get_workspace_service()


THINK_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前正在执行一个任务计划中的某个步骤。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请针对当前任务进行思考：
1. 分析任务目标
2. 结合之前的执行结果（如果有）
3. 给出你的思考过程和结论

请简洁清晰地回答，不要过于冗长。"""

CHAT_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前需要向用户输出回复。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请直接向用户输出回复内容：
- 语言简洁清晰
- 直接回答用户问题
- 不要输出思考过程，只输出最终回复
- 使用友好、专业的语气"""

INTENT_ANALYSIS_PROMPT = """你是一个专业的需求分析专家。请分析用户的输入，识别其真实意图和需求。

{tool_prompt}

## 意图类型说明
- develop: 开发新功能、编写代码、创建文件
- explore: 探索代码库、查找文件、理解项目结构
- review: 代码审查、检查问题、优化建议
- question: 问答、咨询、解释说明
- debug: 调试问题、修复错误、排查故障
- refactor: 重构代码、优化结构、改进设计
- other: 其他类型

## 输出格式要求
请严格按照以下 JSON 格式输出：

```json
{
  "intent_type": "意图类型",
  "summary": "需求摘要（一句话描述核心需求）",
  "key_points": ["关键点1", "关键点2"],
  "suggested_tools": ["建议使用的工具1", "建议使用的工具2"],
  "complexity": "simple/medium/complex",
  "confidence": 0.95
}
```

## 分析要点
1. 准确识别用户的主要意图
2. 提取核心需求点
3. 判断任务复杂度
4. 给出置信度（0-1之间）
5. 只输出 JSON，不要有其他文字
6. suggested_tools 只能从上面的可用工具列表中选择，不要使用列表中不存在的工具"""

PLAN_SYSTEM_PROMPT_BASE = """你是一个专业的软件工程师助手。你的任务是根据用户需求生成一个清晰的执行计划。

{tool_prompt}

## 任务阶段说明
每个任务必须属于以下四个阶段之一：
1. **research** - 研究阶段：探索代码库，理解问题，收集信息
2. **synthesis** - 综合阶段：综合研究结果，制定实现规范，设计解决方案
3. **implementation** - 实现阶段：实现代码，执行工具，应用更改
4. **verification** - 验证阶段：运行测试，验证功能，检查质量

## 输出格式要求
你必须严格按照以下 JSON 格式输出，不要有任何其他文字：

```json
{{
  "tasks": [
    {{
      "id": 1,
      "description": "任务描述",
      "phase": "research/synthesis/implementation/verification",
      "tool": "工具名称或null",
      "args": {{"参数名": "参数值"}}或null
    }}
  ]
}}
```

## 注意事项
1. 每个任务必须包含 id, description, phase, tool, args 五个字段
2. phase 必须是 research, synthesis, implementation, verification 之一
3. tool 如果不需要使用工具，设为 null
4. args 如果没有参数，设为 null
5. 只输出 JSON，不要有任何解释或额外文字
6. 任务应该按照阶段顺序排列：research -> synthesis -> implementation -> verification
7. 每个阶段可以有多个任务，但必须保持阶段顺序"""

DIRECTOR_PLAN_SYSTEM_PROMPT = """你是一个软件工程任务规划器。

请只输出高层计划纲要，严格使用 JSON：
{
  "tasks": [
    {
      "description": "步骤描述",
      "goal": "该步骤要达成的目标",
      "done_when": "满足什么条件说明该步骤完成",
      "phase": "research|synthesis|implementation|verification"
    }
  ]
}

要求：
1. 只输出 2-5 个高层步骤
2. 不要在这里生成 tool 或具体 args
3. description 要描述做什么，goal 要描述为什么做，done_when 要描述完成判定
4. 输出必须是 JSON
"""


def build_chat_system_prompt(settings_service=None) -> str:
    prompt = CHAT_SYSTEM_PROMPT
    if settings_service is None:
        return prompt

    try:
        supports_vision = bool(settings_service.get("llm:supports_vision"))
    except Exception:
        supports_vision = False

    if supports_vision:
        prompt += "\n\n你使用的大模型是原生多模态模型，支持图像理解。"
        prompt += "\n如果当前消息中已经提供图片，请直接基于图片内容进行分析并回答，不要声称缺少图像工具或要求用户再把图片转成文字。"
    return prompt


def build_context_prompt(
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    current_task: str,
) -> str:
    prompt_parts = []

    if parent_chain_messages:
        prompt_parts.append("[历史对话]")
        for msg in parent_chain_messages:
            role = msg.get("role", "user")
            content = build_prompt_safe_text(msg)
            prompt_parts.append(f"{role}: {content}")
        prompt_parts.append("")

    if current_conversation_messages:
        prompt_parts.append("[当前对话内历史]")
        for msg in current_conversation_messages:
            role = msg.get("role", "user")
            content = build_prompt_safe_text(msg)
            prompt_parts.append(f"{role}: {content}")
        prompt_parts.append("")

    prompt_parts.append("[当前任务]")
    prompt_parts.append(current_task)

    return "\n".join(prompt_parts)


def build_direct_chat_messages(
    task_description: str,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    multimodal_parts: Optional[List[dict]] = None,
    message_context: Optional[dict] = None,
) -> List[dict]:
    if multimodal_parts:
        workspace_dir = None
        if message_context and message_context.get("workspace_id"):
            workspace_dir = workspace_service.get_workspace_dir(message_context.get("workspace_id"))
        resolved_parts = resolve_runtime_parts(multimodal_parts, workspace_dir)
        messages = list(parent_chain_messages)
        messages.extend(current_conversation_messages)
        messages.append({
            "role": "user",
            "parts": resolved_parts,
            "content": build_prompt_safe_text(resolved_parts),
        })
        return messages

    full_prompt = build_context_prompt(
        parent_chain_messages,
        current_conversation_messages,
        f"请向用户输出回复: {task_description}",
    )
    return [{"role": "user", "content": full_prompt}]


def build_tool_schema_prompt(tool_names: List[str]) -> str:
    from service.agent_service.tools import ALL_TOOLS

    schema_lines = ["工具列表："]
    for tool_name in tool_names:
        tool_meta = ALL_TOOLS.get(tool_name)
        if not tool_meta:
            continue
        params = tool_meta.get("params", "")
        if params:
            schema_lines.append(params)
    return "\n".join(schema_lines)


def format_todo_prompt_block(todos: List[str], current_todo_index: int) -> str:
    if not todos:
        return ""

    lines = ["当前 TODO 列表（完整状态）:"]
    for idx, todo in enumerate(todos):
        marker = " <= 当前执行项" if idx == current_todo_index else ""
        lines.append(f"- [{idx}] {todo}{marker}")
    lines.append(f"doingIdx={current_todo_index}")
    lines.append("如果任务明显是多步骤、阶段化，或执行中发现当前任务过大/过难，应使用 update_todo 一次性写入或重写完整 todo 列表；如果任务本身是单步骤且简单，则不要使用 todo 工具。")
    return "\n".join(lines)


def get_plan_system_prompt(agent_type: str = "director_agent", settings_service=None) -> str:
    tool_prompt = generate_tool_prompt(agent_type, settings_service)
    return PLAN_SYSTEM_PROMPT_BASE.format(tool_prompt=tool_prompt)


def format_parent_chain_block(parent_chain_messages: List[dict]) -> str:
    if not parent_chain_messages:
        return ""

    lines = ["## 历史对话记录", ""]
    lines.append("以下是之前对话分支的历史记录，供参考：")
    lines.append("")

    for msg in parent_chain_messages:
        role = msg.get("role", "unknown")
        content = build_prompt_safe_text(msg)
        role_label = "用户" if role == "user" else "助手" if role == "assistant" else role
        lines.append(f"**{role_label}**: {content}")

    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def format_current_conversation_block(current_conversation_messages: List[dict]) -> str:
    if not current_conversation_messages:
        return ""

    lines = ["## 当前对话内历史内容", ""]
    lines.append("以下是当前对话内之前的交互记录：")
    lines.append("")

    for msg in current_conversation_messages:
        role = msg.get("role", "unknown")
        content = build_prompt_safe_text(msg)
        role_label = "用户" if role == "user" else "助手" if role == "assistant" else role
        lines.append(f"**{role_label}**: {content}")

    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def format_current_question(user_message: str) -> str:
    return f"""## 当前用户问题

**用户**: {user_message}

"""


def build_intent_analysis_messages(
    user_message: str,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    agent_type: str = "director_agent",
    settings_service=None,
) -> tuple[str, List[dict]]:
    tool_prompt = generate_tool_prompt(agent_type, settings_service)
    system_prompt = INTENT_ANALYSIS_PROMPT.format(tool_prompt=tool_prompt)
    prompt = (
        f"{format_parent_chain_block(parent_chain_messages)}"
        f"{format_current_conversation_block(current_conversation_messages)}"
        f"{format_current_question(user_message)}"
        "请分析以上用户当前问题的意图。"
    )
    return system_prompt, [{"role": "user", "content": prompt}]


def build_plan_generation_messages(
    user_message: str,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    intent_analysis: Optional[dict] = None,
    agent_type: str = "director_agent",
    settings_service=None,
) -> tuple[str, List[dict]]:
    system_prompt = get_plan_system_prompt(agent_type, settings_service)

    intent_context = ""
    if intent_analysis:
        intent_context = f"""
## 意图分析结果
- 意图类型: {intent_analysis.get('intent_type', 'unknown')}
- 需求摘要: {intent_analysis.get('summary', '')}
- 关键点: {', '.join(intent_analysis.get('key_points', []))}
- 建议工具: {', '.join(intent_analysis.get('suggested_tools', []))}
- 复杂度: {intent_analysis.get('complexity', 'medium')}
"""

    prompt = (
        f"{format_parent_chain_block(parent_chain_messages)}"
        f"{format_current_conversation_block(current_conversation_messages)}"
        f"{format_current_question(user_message)}"
        f"{intent_context}"
        "请根据以上用户当前问题生成执行计划，包含 2-5 个任务，严格按照 JSON 格式输出。"
    )
    return system_prompt, [{"role": "user", "content": prompt}]


def build_special_tool_prompt(
    task_description: str,
    previous_results: List[str],
    final_instruction: str,
) -> str:
    context_parts = [f"当前任务: {task_description}"]

    if previous_results:
        context_parts.append("\n--- 之前任务的执行结果 ---")
        for i, prev_result in enumerate(previous_results, 1):
            truncated = prev_result[:500] + "..." if len(prev_result) > 500 else prev_result
            context_parts.append(f"任务{i}结果:\n{truncated}")
        context_parts.append("---\n")

    context_parts.append(final_instruction)
    return "\n".join(context_parts)


def build_special_tool_messages(
    task_description: str,
    previous_results: List[str],
    final_instruction: str,
    parent_chain_messages: Optional[List[dict]] = None,
) -> List[dict]:
    prompt = build_special_tool_prompt(task_description, previous_results, final_instruction)
    messages = list(parent_chain_messages or [])
    messages.append(build_user_message("user", prompt))
    return messages


def build_director_plan_messages(user_message: str) -> tuple[str, List[dict]]:
    return DIRECTOR_PLAN_SYSTEM_PROMPT, [{"role": "user", "content": f"请为以下任务生成高层执行计划：\n\n{user_message}"}]
