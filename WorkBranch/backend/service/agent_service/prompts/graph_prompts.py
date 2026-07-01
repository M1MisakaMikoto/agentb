from typing import Any, List, Optional, Tuple

from service.session_service.message_content import (
    build_prompt_safe_text,
    build_user_message,
    resolve_runtime_parts,
)
from singleton import get_workspace_service

from .templates import (
    THINK_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    DIRECT_SYSTEM_PROMPT,
    PLAN_MODE_SYSTEM_PROMPT,
    INTENT_ANALYSIS_PROMPT,
)
from .error_injection import ToolCallError, format_error_for_prompt

workspace_service = get_workspace_service()


# 以下常量保留在 graph_prompts.py 中（独有或用于特定场景）
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
    parent_chain_messages,
    current_conversation_messages,
    current_task: str,
    message_context=None,
) -> str:
    """
    构建上下文提示词（已增强：支持自动压缩）
    
    新特性：
    - ✅ 自动检测是否需要压缩
    - ✅ 使用卷积压缩算法处理长对话
    - ✅ 保持向后兼容
    """
    try:
        from .base.message_processor import MessageProcessor
        
        processor = MessageProcessor(
            message_context.get("settings_service") if message_context else None
        )
        
        parts = []
        
        # 父链消息（带自动压缩）
        if parent_chain_messages:
            parent_section = processor.process_conversation_context(
                messages=parent_chain_messages,
                source_type="parent_chain",
                message_context=message_context
            )
            if parent_section:
                parts.append(parent_section)
        
        # 当前对话消息（带自动压缩）
        if current_conversation_messages:
            current_section = processor.process_conversation_context(
                messages=current_conversation_messages,
                source_type="current_conversation",
                message_context=message_context
            )
            if current_section:
                parts.append(current_section)
        
        # 当前任务
        parts.append(f"[当前任务]\n{current_task}")
        
        return "\n\n".join(parts)
        
    except Exception as e:
        print(f"[build_context_prompt] 新架构调用失败，回退到旧实现: {e}")
        return _build_context_prompt_fallback(
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
            current_task=current_task,
        )


def _build_context_prompt_fallback(
    parent_chain_messages,
    current_conversation_messages,
    current_task: str,
) -> str:
    """回退到旧实现的上下文构建"""
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


def build_tool_schema_prompt(tool_names: List[str], agent_type: str = "unknown") -> str:
    from service.agent_service.tools import ALL_TOOLS

    schema_lines = ["工具列表："]
    for tool_name in tool_names:
        tool_meta = ALL_TOOLS.get(tool_name)
        if not tool_meta:
            continue
        params = tool_meta.get("params", "")
        if params:
            schema_lines.append(params)
    result = "\n".join(schema_lines)

    try:
        import datetime
        from pathlib import Path
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        # 使用绝对路径，避免 Windows 上的路径问题
        backend_dir = Path(__file__).parent.parent.parent
        log_path = backend_dir / 'llm_decision_trace.log'
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] === TOOL LIST FOR {agent_type} ===\n")
            f.write(f"Total tools: {len(tool_names)}\n")
            f.write(f"Tool names: {tool_names}\n")
            f.write(f"\n--- FULL TOOL PROMPT ---\n{result}\n")
            f.write(f"{'='*80}\n")
            f.flush()
    except Exception:
        pass

    return result


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
    from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools
    allowed_tools = get_allowed_tools(agent_type, settings_service)
    tool_prompt = build_tool_schema_prompt(allowed_tools, agent_type=agent_type)
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


def generate_prompt(
    agent_type: str,
    mode: str,
    user_message: str,
    workspace_id: str,
    iteration_count: int,
    max_iterations: int,
    tool_schema_prompt: str,
    tool_history: List[dict],
    last_tool_result: Optional[str],
    todos: List[str],
    current_todo_index: int,
    plan_content: Optional[str] = None,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None,
    last_error: Optional[ToolCallError] = None,
) -> Tuple[str, str]:
    """
    统一的提示词生成入口（已简化：直接使用核心组件）

    简化后的调用方式：
    - 直接导入 System Prompt 常量
    - 使用 MessageProcessor 构建 User Message
    - 使用 UserTemplateManager 构建静态区块

    Args:
        agent_type: agent类型 (director_agent, prediction_agent等)
        mode: 执行模式 (DIRECT, PLAN)
        user_message: 用户原始请求
        workspace_id: 工作区ID
        iteration_count: 当前执行轮次（不再显示在prompt中）
        max_iterations: 最大执行轮次
        tool_schema_prompt: 工具列表提示词
        tool_history: 工具执行历史
        last_tool_result: 最近工具结果
        todos: todo列表
        current_todo_index: 当前todo索引
        plan_content: 计划文件内容
        parent_chain_messages: 父链消息
        current_conversation_messages: 当前对话消息
        last_error: 上一次工具调用的错误信息（如果有）

    Returns:
        Tuple[str, str]: (system_prompt, user_message)
    """
    from .base.message_processor import MessageProcessor
    from .templates.user_templates import UserTemplateManager

    processor = MessageProcessor()

    # 1. 获取 System Prompt（简化后直接使用 _get_system_prompt）
    system_prompt = _get_system_prompt(
        agent_type=agent_type,
        mode=mode,
        tool_prompt=tool_schema_prompt
    )

    # 2. 构建 User Message

    # 2a. 静态区域（工具列表）
    static_section = UserTemplateManager.build_static_section()

    # 2b. 动态区域（元数据）
    dynamic_section = processor.build_dynamic_section(
        user_message=user_message,
        workspace_id=workspace_id,
        todos=todos,
        current_todo_index=current_todo_index,
        plan_content=plan_content,
        include_iteration=False  # 已执行轮次默认不显示
    )

    # 2c. 对话上下文（压缩）- Director Agent 专属
    conversation_context = ""

    if parent_chain_messages:
        parent_context = processor.process_conversation_context(
            messages=parent_chain_messages,
            source_type="parent_chain"
        )
        if parent_context:
            conversation_context += parent_context + "\n"

    if current_conversation_messages:
        current_context = processor.process_conversation_context(
            messages=current_conversation_messages,
            source_type="current_conversation"
        )
        if current_context:
            conversation_context += current_context

    # 3. PLAN 模式额外指令
    if mode.upper() == "PLAN":
        dynamic_section += "\n\n" + UserTemplateManager.build_plan_mode_suffix()

    # 4. 工具历史（统一区块，含时间标识）
    tool_history_section = ""
    history_block = _format_tool_history(tool_history)
    if tool_history:
        tool_history_section = f"\n\n{history_block}\n"

    # 5. 拼接 User Message
    user_message_text = processor.build_full_user_message(
        static_content=static_section,
        dynamic_content=dynamic_section,
        conversation_context=conversation_context.strip()
    )

    # 6. 追加工具历史
    if tool_history_section:
        user_message_text += tool_history_section

    # 7. 追加用户原始问题（倒数第二位，利用头尾效应）
    if user_message:
        user_message_text += format_current_question(user_message)

    # 8. 注入错误信息（最后）
    if last_error:
        error_prompt = format_error_for_prompt(last_error)
        user_message_text += "\n\n" + error_prompt

    return system_prompt, user_message_text


def _get_system_prompt(agent_type: str, mode: str, tool_prompt: str = "") -> str:
    """
    根据 agent_type 和 mode 获取对应的 System Prompt

    简化后直接返回常量，不再委托给 DirectorPromptBuilder
    """
    from .agent_prompts import (
        PREDICTION_AGENT_PROMPT,
        EXPLORE_AGENT_PROMPT,
        REVIEW_AGENT_PROMPT,
    )

    if agent_type == "prediction_agent":
        template = PREDICTION_AGENT_PROMPT
        if tool_prompt:
            return f"{template}\n\n<!-- 额外可用工具 -->\n{tool_prompt}"
        return template

    elif agent_type == "director_agent":
        if mode.upper() == "PLAN":
            return PLAN_MODE_SYSTEM_PROMPT
        # 只有当 template 包含 {tool_prompt} 占位符时才格式化
        if tool_prompt and "{tool_prompt}" in DIRECT_SYSTEM_PROMPT:
            return DIRECT_SYSTEM_PROMPT.format(tool_prompt=tool_prompt)
        return DIRECT_SYSTEM_PROMPT

    elif agent_type == "explore_agent":
        return EXPLORE_AGENT_PROMPT

    elif agent_type == "review_agent":
        return REVIEW_AGENT_PROMPT

    elif agent_type == "thinking":
        return THINK_SYSTEM_PROMPT

    elif agent_type == "chat":
        return CHAT_SYSTEM_PROMPT

    else:
        # 默认使用 DIRECT_SYSTEM_PROMPT，对所有 agent 类型都注入工具列表
        if tool_prompt and "{tool_prompt}" in DIRECT_SYSTEM_PROMPT:
            return DIRECT_SYSTEM_PROMPT.format(tool_prompt=tool_prompt)
        return DIRECT_SYSTEM_PROMPT


def _generate_prompt_fallback(
    agent_type: str,
    mode: str,
    user_message: str,
    workspace_id: str,
    iteration_count: int,
    max_iterations: int,
    tool_schema_prompt: str,
    tool_history: List[dict],
    last_tool_result: Optional[str],
    todos: List[str],
    current_todo_index: int,
    plan_content: Optional[str] = None,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None,
) -> Tuple[str, str]:
    """回退到旧实现的提示词生成（保持向后兼容）"""
    system_prompt = _get_system_prompt(agent_type, mode, tool_schema_prompt)
    
    user_msg = _build_user_message(
        agent_type=agent_type,
        mode=mode,
        user_message=user_message,
        workspace_id=workspace_id,
        iteration_count=iteration_count,
        max_iterations=max_iterations,
        tool_schema_prompt=tool_schema_prompt,
        tool_history=tool_history,
        last_tool_result=last_tool_result,
        todos=todos,
        current_todo_index=current_todo_index,
        plan_content=plan_content,
    )
    
    context_prompt = build_context_prompt(
        parent_chain_messages or [],
        current_conversation_messages or [],
        user_msg,
    )
    
    return system_prompt, context_prompt


def _build_user_message(
    agent_type: str,
    mode: str,
    user_message: str,
    workspace_id: str,
    iteration_count: int,
    max_iterations: int,
    tool_schema_prompt: str,
    tool_history: List[dict],
    last_tool_result: Optional[str],
    todos: List[str],
    current_todo_index: int,
    plan_content: Optional[str] = None,
) -> str:
    """组装user message"""
    static_content = (
        f"{tool_schema_prompt}\n\n"
        "注意：只有当 todo 列表非空时，你才应围绕 todo 执行；如果当前没有 todo 且任务明显多步骤/阶段化，可以先使用 update_todo 写入完整 todo 列表。"
        "如果 todo 列表非空，你应继续通过 update_todo 覆盖更新完整 todo 列表和 doingIdx；如果任务拆分发生变化，也应通过 update_todo 一次性重写。"
        "默认按 DIRECT 执行；如果你在执行过程中发现任务明显复杂、多阶段、跨文件、需要先输出方案，才调用 switch_execution_mode 把模式切到 PLAN。"
        "如果上一条历史对话提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，那么你应先使用 read_file 读取该 plan.md，再严格遵守该计划执行。"
        "除非用户明确要求查看计划文件，否则不要为了展示而读取 plan.md。"
        "请只决定下一步动作，并以 JSON 形式返回：如果需要继续操作，返回一个 tool 调用；如果当前 todo 已完成，返回 kind=step_done；如果需要向用户输出最终回复，使用 chat 工具；如果无法继续，返回 kind=blocked。\n\n"
    )
    
    history_block = _format_tool_history(tool_history)
    todo_intro = _format_todo_intro(todos, current_todo_index)
    plan_intro = _format_plan_intro(plan_content)

    dynamic_content = (
        f"当前工作区ID: {workspace_id}\n"
        f"已执行轮次: {iteration_count}/{max_iterations}\n"
        f"{plan_intro}"
        f"{todo_intro}"
        f"{history_block}\n"
    )

    # 用户原始问题放到倒数第二位（利用头尾效应）
    dynamic_content += format_current_question(user_message)

    if mode == "PLAN":
        dynamic_content += (
            "请只决定下一步动作，并以 JSON 形式返回：如果需要继续操作，返回一个 tool 调用；如果计划已完成，返回 kind=step_done；如果需要向用户输出回复，使用 chat 工具；如果无法继续，返回 kind=blocked。"
        )
    
    return static_content + dynamic_content


def _format_tool_history(tool_history: List[dict]) -> str:
    """格式化工具历史 - 使用时间标识，最新条目显示完整结果"""
    if not tool_history:
        return "(暂无工具执行历史)"

    def _make_summary(text: str, limit: int = 2000) -> str:
        """简单的文本摘要"""
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."

    recent_items = tool_history[-5:]
    history_lines = ["工具执行记录 (时间倒序):", ""]

    for idx, item in enumerate(recent_items):
        result_text = str(item.get("result") or "")
        # 最新一条显示完整结果，其余使用摘要
        if idx == 0:
            time_tag = "[最新]"
        else:
            time_tag = f"[t-{idx}]"
            if len(result_text) > 2000:
                result_text = _make_summary(result_text)

        history_lines.append(f"{time_tag} tool={item.get('tool_name')} args={item.get('args')}")
        history_lines.append(f"     result={result_text}")
        # 补全 error 字段：当 result 为空但 error 有值时，输出错误信息供 LLM 决策
        error_text = str(item.get("error") or "")
        if error_text:
            history_lines.append(f"     错误: {error_text}")
        history_lines.append("")

    return "\n".join(history_lines)


def _format_last_result(last_tool_result: Optional[str]) -> str:
    """格式化最近工具结果 - 移除截断"""
    if not last_tool_result:
        return "(无)"
    # 不再截断，让 LLM 看到完整内容
    return last_tool_result


def _format_todo_intro(todos: List[str], current_todo_index: int) -> str:
    """格式化todo提示"""
    if not todos:
        return ""
    todo_block = format_todo_prompt_block(todos, current_todo_index)
    return f"\n\n{todo_block}\n\n"


def _format_plan_intro(plan_content: Optional[str]) -> str:
    """格式化计划文件提示"""
    if not plan_content:
        return ""
    return (
        f"\n\n当前工作区存在计划文件: plan.md\n"
        "如果上一条历史对话提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，"
        "那么你应主动使用 read_file 读取该 plan.md，再严格遵守该计划执行；否则不要因为计划文件存在就默认按计划执行。\n"
    )



def build_intent_analysis_messages(
    user_message: str,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    agent_type: str = "director_agent",
    settings_service=None,
    message_context: Optional[dict] = None,
) -> tuple[str, List[dict]]:
    from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools
    allowed_tools = get_allowed_tools(agent_type, settings_service)
    tool_prompt = build_tool_schema_prompt(allowed_tools, agent_type=agent_type)
    system_prompt = INTENT_ANALYSIS_PROMPT.format(tool_prompt=tool_prompt)
    prompt = (
        f"{format_parent_chain_block(parent_chain_messages, message_context)}"
        f"{format_current_conversation_block(current_conversation_messages, message_context)}"
        f"{format_current_question(user_message)}"
        "请分析以上用户当前问题的意图。"
    )
    
    # Log the complete prompt sent to LLM
    try:
        import datetime
        from pathlib import Path
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        # 使用绝对路径，避免 Windows 上的路径问题
        backend_dir = Path(__file__).parent.parent.parent
        log_path = backend_dir / 'llm_decision_trace.log'
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] === INTENT ANALYSIS PROMPT ===\n")
            f.write(f"Agent Type: {agent_type}\n")
            f.write(f"User Message ({len(user_message)} chars): {user_message}\n")
            f.write(f"\n--- SYSTEM PROMPT ({len(system_prompt)} chars) ---\n{system_prompt}\n")
            f.write(f"\n--- USER PROMPT ({len(prompt)} chars) ---\n{prompt}\n")
            f.write(f"{'='*80}\n")
            f.flush()
    except Exception as e:
        pass
    
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
            # 🔧 修复：支持字典格式的 previous_results（包含 tool_name 和 result）
            if isinstance(prev_result, dict):
                tool_name = prev_result.get("tool_name", "unknown")
                result = prev_result.get("result", "")
                reason = prev_result.get("reason", "")
                # 格式化：工具名 + 结果内容
                formatted_result = f"[工具: {tool_name}]"
                if reason:
                    formatted_result += f"\n原因: {reason}"
                formatted_result += f"\n结果:\n{result}"
                context_parts.append(f"任务{i}结果:\n{formatted_result}")
            else:
                # 字符串格式，兼容旧代码
                context_parts.append(f"任务{i}结果:\n{prev_result}")
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
