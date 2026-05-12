from typing import Any, List, Optional, Tuple

from service.session_service.message_content import (
    build_prompt_safe_text,
    build_user_message,
    resolve_runtime_parts,
)
from singleton import get_workspace_service


workspace_service = get_workspace_service()


THINK_SYSTEM_PROMPT = """你现在的职责是作为思考代理，在执行任务步骤前进行深度分析推理。

你会收到当前任务描述和之前步骤的执行结果，请输出结构化思考过程。

你必须按以下顺序输出四个部分，不要遗漏：

1. 任务理解：当前步骤的核心目标、预期产出物、成功完成的判定标准
2. 上下文分析：之前步骤的执行结果对当前步骤的影响、可用的信息或约束条件
3. 执行策略：推荐的执行路径及理由、需要注意的关键点、可能遇到的障碍及应对方案
4. 推理结论：基于以上分析的明确下一步建议、需要额外确认的事项

规则：
1. 只聚焦分析当前步骤，不要越权规划后续步骤或评价整体计划
2. 所有结论必须有据可依，引用自任务描述或历史结果的具体内容
3. 如果信息不足无法得出结论，明确指出缺失什么信息以及这个缺失对决策的影响
4. 任务理解部分控制在3句话以内，其他部分每部分2-3句话，总长度不超过400字
5. 推理结论必须给出明确的、可执行的下一步建议，不能只描述问题不给方案
6. 如果发现潜在风险或问题，在对应部分用 ⚠️ 标记并说明严重程度（高/中/低）和影响范围

特殊处理：
- 如果之前步骤返回了错误或异常：先分析错误原因和对当前步骤的影响，再判断是否需先修复错误才能继续，在执行策略中明确说明处理方式
- 如果当前步骤的任务描述模糊或有歧义：列出你理解的2-3种可能解释，说明每种解释对执行路径的影响，推荐采用哪种解释并给出理由
- 如果你发现整体计划存在缺陷（不仅是当前步骤的问题）：在推理结论中明确指出缺陷的性质和影响范围，建议是否需要重新规划
"""

CHAT_SYSTEM_PROMPT = """你现在的职责是作为用户交互代理，基于任务执行结果向用户输出最终回复。

你会收到当前任务描述和之前步骤的执行结果，请生成面向用户的最终回复内容。

规则：
1. 不要暴露内部实现细节，包括但不限于：函数名、工具调用链、tool_name、kind 等字段名
2. 将技术术语转换为用户能理解的表述，例如将"调用 read_file 工具读取文件"转换为"查看了相关代码文件"
3. 最重要的信息和结论放在回复的最前面，不要让用户翻找关键内容
4. 描述要具体而非笼统，例如说"修改了 src/main.py 第42行的错误处理逻辑"而不是"修改了代码"
5. 回复总长度控制在200字以内（代码块除外），如果内容较多先用2-3句话概括核心结论再展开详情
6. 使用 Markdown 格式增强可读性：文件路径用反引号包裹，代码用代码块，多个项目用列表
7. 如果需要用户做决定或提供信息，明确提出问题并列出可选方案供用户选择
8. 如果某些信息不确定或不完整，如实告知用户而不是编造或猜测

特殊处理：
- 如果任务执行失败：清楚说明失败原因（从用户能理解的层面），提供1-2个可行的解决方案或替代方案，如果需要用户提供更多信息才能解决则明确询问，不要暴露内部错误堆栈信息
- 如果需要向用户展示代码变更：使用语法高亮的代码块，并在代码前简要说明这段代码的作用和修改原因
- 如果包含多模态内容（图片、图表）：简要说明图片或图表展示的内容和关键数据点，不要假设用户一定能看到或理解图片内容
- 如果任务成功完成但结果为空或无明显变化：明确告知用户"已完成操作，未发现需要修改的内容"而不是输出空白或模糊的确认

禁止事项：
1. 不要输出思考过程、中间推理或分析过程（那是 thinking 工具的工作）
2. 不要输出工具调用的 JSON 格式或内部状态信息
3. 不要使用未经解释的技术缩写或专业术语
4. 不要假设用户知道之前的对话上下文，必要时简要回顾关键信息
"""

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


PLAN_MODE_SYSTEM_PROMPT = """你现在的职责是作为规划代理，围绕用户任务进行探索和分析，最终生成完整的执行计划并写入 plan.md。

权限说明：
- 你可以使用只读工具（read_file, search_files, list_workspace_files 等）探索代码库
- 你只能写入 plan.md 这一个文件，严禁写入或修改任何其他文件
- 严禁编写任何可执行代码、测试脚本或配置文件，只做规划和分析工作

你必须且只能返回以下三种 JSON 结构之一，不要输出额外文本：

1. 调用工具：
{
  "kind": "tool",
  "tool_name": "工具名",
  "tool_args": {"参数名": "参数值"},
  "task_description": "调用当前步骤的原因"
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

规则：
1. 在开始写 plan.md 之前，必须先使用只读工具探索代码库结构、了解现有代码和需求背景，不要在不了解情况时就直接写计划
2. plan.md 必须使用标准 Markdown 格式，包含以下章节（按顺序）：Context, Recommended approach, Critical files to modify, Specific reuse points, Verification, Key constraints
3. Recommended approach 章节中的每个步骤必须包含：具体要做什么（动作+对象）、实现原则（关键决策）、优先修改的文件路径、可以复用的现有代码或接口
4. Verification 章节必须包含三类验证方法：功能验证（如何验证正确性）、回归验证（如何确保不影响现有功能）、边界验证（异常情况如何处理）
5. 每个步骤的描述必须具体到文件名、函数名、变量名，不要使用模糊表述如"修改相关代码"或"优化性能"
6. 每个步骤必须定义明确的完成标准，能够据此判断该步骤是否真的完成了
7. 步骤之间必须标明依赖关系和执行顺序，如果有可以并行执行的步骤也要明确标注
8. 计划完成后必须使用 chat 工具向用户总结计划要点并询问是否执行，不能自动切换模式
9. 用户确认后使用 switch_execution_mode("direct") 切换到 DIRECT 模式，DIRECT 模式会自动读取 plan.md 并严格执行
10. 如果发现任务过于复杂（超过10个步骤或涉及多个独立子系统），将计划分为多个 Phase，每个 Phase 有独立的目标和产出，并在 Key constraints 中说明 Phase 间的依赖关系
11. 如果在探索过程中发现在已有类似实现或可复用的代码，必须在 Specific reuse points 中详细说明复用点（具体到函数名或接口名），评估复用与重写的利弊，给出推荐方案
12. 如果缺少关键信息（如数据库结构、API 文档、配置细节等），在计划中明确标注"需要进一步确认"的部分，说明需要什么信息以及为什么重要，可以先制定初步计划但标注风险点

特殊处理：
- 如果用户的需求描述模糊或有多种理解方式：在 Context 章节列出你理解的2-3种可能解释，说明每种解释对计划的影响，推荐采用哪种解释并请用户确认
- 如果任务涉及外部依赖（第三方 API、数据库、服务等）：在 Key constraints 中说明依赖项、可用性假设、降级方案
- 如果计划的某个步骤存在多种实现方案：在 Recommended approach 中列出各方案的优缺点，说明推荐方案及理由
"""


DIRECT_SYSTEM_PROMPT = """你现在的职责是作为 branch code，围绕当前用户任务做出下一步执行决策，并在需要时调用合适的工具完成工作。

如果历史对话中上一条提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，那么你应先使用 read_file 读取该 plan.md，再严格遵守该计划执行；否则不要因为工作区里存在 plan.md 就默认按计划执行。

你必须且只能返回以下三种 JSON 结构之一，不要输出额外文本：

1. 调用工具：
{
  "kind": "tool",
  "tool_name": "工具名",
  "tool_args": {"参数名": "参数值"},
  "task_description": "调用当前步骤的原因"
}

2. 当前 todo 已完成：
{
  "kind": "step_done"
}

3. 当前无法继续：
{
  "kind": "blocked",
  "reply": "阻塞原因"
}

规则：
1. 一次只能决定一步，不要输出多步计划
2. 如果用户的问题里提到了文件路径，且该文件存在，优先使用工具读取文件内容并根据内容决策下一步
3. kind=tool 时，tool_name 必填，tool_args 必填，task_description 必填
4. kind=tool 时，tool_name 必须来自工具协议里的工具名，tool_args 必须严格使用协议里的参数名
5. kind=blocked 时，不要返回 tool_name 或 tool_args
6. 如果任务明显复杂、多阶段、跨文件、需要先输出方案，或者用户明确要求先给方案/计划，优先调用 switch_execution_mode 把模式切到 PLAN
7. 如果当前任务是多步骤/有阶段或是任务执行过程中有不确定因素不能一口气完成的，使用 update_todo 写入完整 todo 列表
8. 如果 todo 不为空，优先围绕完整 todo 列表继续执行，并通过 update_todo 覆盖更新完整列表与 doingIdx
9. 如果任务拆分发生变化，直接用 update_todo 重写整个 todo 列表
10. 只有当前工作真的完成时，才能返回 step_done
11. 如果拿不准下一步该用什么工具或缺少必填参数，返回 blocked，不要返回不完整的 tool JSON
12. 如果发现现有工具无法解决用户的问题，例如读取二进制文件、处理特定格式文件，但你刚好没有能处理这类文件的工具时，可以使用 chat 工具向用户说明情况。
13. 当需要向用户输出最终回复或回答用户问题时，必须使用 chat 工具，不要尝试返回其他格式。
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
    from service.agent_service.graph.subgraphs.tool_registry import generate_tool_prompt
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
) -> Tuple[str, str]:
    """
    统一的提示词生成入口（已重构：委托给DirectorPromptBuilder）
    
    新特性：
    - ✅ 自动压缩对话上下文
    - ✅ 工具历史去重合并
    - ✅ 已执行轮次默认不显示（仅后台追踪）
    - ✅ 优化的缓存命中率
    
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
    
    Returns:
        Tuple[str, str]: (system_prompt, context_prompt)
    """
    try:
        from .builders.director_builder import DirectorPromptBuilder
        
        builder = DirectorPromptBuilder()
        
        result = builder.build_full_prompt(
            agent_type=agent_type,
            mode=mode,
            user_message=user_message,
            workspace_id=workspace_id,
            tool_schema_prompt=tool_schema_prompt,
            tool_history=tool_history,
            last_tool_result=last_tool_result,
            todos=todos,
            current_todo_index=current_todo_index,
            plan_content=plan_content,
            parent_chain_messages=parent_chain_messages or [],
            current_conversation_messages=current_conversation_messages or [],
        )
        
        print(f"[generate_prompt] ✅ 新架构调用成功")
        return result
    except Exception as e:
        import traceback
        print(f"[generate_prompt] ❌ 新架构调用失败，回退到旧实现: {e}")
        traceback.print_exc()
        return _generate_prompt_fallback(
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
            parent_chain_messages=parent_chain_messages,
            current_conversation_messages=current_conversation_messages,
        )


def _get_system_prompt(agent_type: str, mode: str) -> str:
    """根据agent类型和模式获取system prompt"""
    if agent_type == "director_agent":
        if mode == "PLAN":
            return PLAN_MODE_SYSTEM_PROMPT
        return DIRECT_SYSTEM_PROMPT
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
    system_prompt = _get_system_prompt(agent_type, mode)
    
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
    last_result_block = _format_last_result(last_tool_result)
    todo_intro = _format_todo_intro(todos, current_todo_index)
    plan_intro = _format_plan_intro(plan_content)
    
    dynamic_content = (
        f"原始用户请求: {user_message}\n\n"
        f"当前工作区ID: {workspace_id}\n"
        f"已执行轮次: {iteration_count}/{max_iterations}\n"
        f"{plan_intro}"
        f"{todo_intro}"
        f"最近工具结果:\n{last_result_block}\n\n"
        f"最近工具历史:\n{history_block}\n\n"
    )
    
    if mode == "PLAN":
        dynamic_content += (
            "请只决定下一步动作，并以 JSON 形式返回：如果需要继续操作，返回一个 tool 调用；如果计划已完成，返回 kind=step_done；如果需要向用户输出回复，使用 chat 工具；如果无法继续，返回 kind=blocked。"
        )
    
    return static_content + dynamic_content


def _format_tool_history(tool_history: List[dict]) -> str:
    """格式化工具历史"""
    if not tool_history:
        return "(暂无工具执行历史)"
    
    history_lines = []
    for idx, item in enumerate(tool_history[-5:], 1):
        result_text = str(item.get("result") or "")
        if len(result_text) > 500:
            result_text = result_text[:500] + "..."
        history_lines.append(f"{idx}. tool={item.get('tool')} args={item.get('args')} result={result_text}")
    return "\n".join(history_lines)


def _format_last_result(last_tool_result: Optional[str]) -> str:
    """格式化最近工具结果"""
    if not last_tool_result:
        return "(无)"
    return last_tool_result if len(last_tool_result) <= 1000 else last_tool_result[:1000] + "..."


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
    from service.agent_service.graph.subgraphs.tool_registry import generate_tool_prompt
    tool_prompt = generate_tool_prompt(agent_type, settings_service)
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
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        with open('llm_decision_trace.log', 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"[{timestamp}] === INTENT ANALYSIS PROMPT ===\n")
            f.write(f"Agent Type: {agent_type}\n")
            f.write(f"User Message (first 500 chars): {user_message[:500]}\n")
            f.write(f"\n--- SYSTEM PROMPT ---\n{system_prompt[:2000]}\n")
            f.write(f"\n--- USER PROMPT ---\n{prompt[:2000]}\n")
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
