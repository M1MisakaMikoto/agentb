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


PLAN_MODE_SYSTEM_PROMPT = """你现在的职责是作为规划代理，围绕当前用户任务进行探索和分析，最终生成一个完整的执行计划。

## 权限说明
- 你可以使用只读工具进行探索
- 你只能写入 plan.md 文件，禁止写入任何其他文件
- 禁止编写任何代码实现，只做规划和分析

## 输出格式
你必须且只能返回以下三种 JSON 结构之一：

1. 调用工具：
{
  "kind": "tool",
  "tool_name": "工具名",
  "tool_args": {"参数名": "参数值"},
  "task_description": "这一步要做什么"
}

2. 计划已完成：
{
  "kind": "step_done"
}

3. 当前无法继续：
{
  "kind": "blocked",
  "reply": "阻塞原因"
}

## 规则
1. 探索阶段：使用只读工具了解代码库、需求背景
2. 规划阶段：将计划写入 plan.md，格式为 Markdown
3. 严禁写入 plan.md 以外的任何文件
4. 严禁编写代码实现，只输出规划文档
5. 完成后使用 chat 工具向用户总结计划并询问是否执行
6. 用户确认后，使用 switch_execution_mode 切换到 DIRECT 模式

## 计划文档结构要求

生成的 plan.md 必须包含以下章节：

### # Context
描述问题背景、当前状态、改造目标。说明为什么要做这个任务，解决什么问题。

### # Recommended approach
分步骤的推荐方案，每步包含：
- **具体要做什么**：清晰描述这一步的目标
- **实现原则**：关键的设计决策和约束
- **优先修改文件**：列出需要改动的文件路径
- **复用点**：可以复用的现有代码/接口

### # Critical files to modify
列出所有需要修改的关键文件路径。

### # Specific reuse points
列出可以复用的现有代码、接口、函数。

### # Verification
验证计划，包含：
- 功能验证：如何验证功能正确
- 回归验证：如何确保不影响现有功能
- 边界验证：异常情况如何处理

### # Key constraints
关键约束和注意事项，避免执行时踩坑。

## 计划质量要求
1. 每个步骤要有明确的完成条件
2. 文件路径要准确，不要猜测不存在的文件
3. 复用点要具体到函数名/接口名
4. 验证计划要可执行，不要泛泛而谈
5. 约束要具体，避免执行时产生歧义
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
        task_description,
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


def format_parent_chain_block(
    parent_chain_messages: List[dict],
    message_context: Optional[dict] = None
) -> str:
    if not parent_chain_messages:
        return ""

    try:
        from singleton import get_compression_service
        compression_service = get_compression_service()
        compressed_messages, _ = compression_service.compress_messages(
            parent_chain_messages,
            message_context=message_context,
            source="parent_chain"
        )
    except Exception:
        compressed_messages = parent_chain_messages

    lines = ["## 历史对话记录", ""]
    lines.append("以下是之前对话分支的历史记录，供参考：")
    lines.append("")

    for msg in compressed_messages:
        role = msg.get("role", "unknown")
        role_label = "用户" if role == "user" else "助手" if role == "assistant" else role
        
        if msg.get("compressed"):
            content = msg.get("content", "")
            lines.append(f"**{role_label}**: {content}")
            lines.append(f"*(已压缩，原始长度: {msg.get('original_length', 0)}字符)*")
        else:
            content = build_prompt_safe_text(msg)
            lines.append(f"**{role_label}**: {content}")

    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def format_current_conversation_block(
    current_conversation_messages: List[dict],
    message_context: Optional[dict] = None
) -> str:
    if not current_conversation_messages:
        return ""

    try:
        from singleton import get_compression_service
        compression_service = get_compression_service()
        compressed_messages, _ = compression_service.compress_messages(
            current_conversation_messages,
            message_context=message_context,
            source="current_conversation"
        )
    except Exception:
        compressed_messages = current_conversation_messages

    lines = ["## 当前对话内历史内容", ""]
    lines.append("以下是当前对话内之前的交互记录：")
    lines.append("")

    for msg in compressed_messages:
        role = msg.get("role", "unknown")
        role_label = "用户" if role == "user" else "助手" if role == "assistant" else role
        
        if msg.get("compressed"):
            content = msg.get("content", "")
            lines.append(f"**{role_label}**: {content}")
            lines.append(f"*(已压缩，原始长度: {msg.get('original_length', 0)}字符)*")
        else:
            content = build_prompt_safe_text(msg)
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
    message_context: Optional[dict] = None,
) -> tuple[str, List[dict]]:
    tool_prompt = generate_tool_prompt(agent_type, settings_service)
    system_prompt = INTENT_ANALYSIS_PROMPT.format(tool_prompt=tool_prompt)
    prompt = (
        f"{format_parent_chain_block(parent_chain_messages, message_context)}"
        f"{format_current_conversation_block(current_conversation_messages, message_context)}"
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
    message_context: Optional[dict] = None,
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
        f"{format_parent_chain_block(parent_chain_messages, message_context)}"
        f"{format_current_conversation_block(current_conversation_messages, message_context)}"
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
