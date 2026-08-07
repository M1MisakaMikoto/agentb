from ..agent_definition import AgentDefinition, AgentPrompt, AgentMeta


PLAN_AGENT_PROMPT = (
    "你是一个计划子代理。根据任务描述生成一份可执行的结构化执行计划。\n"
    "计划要求：\n"
    "一、计划必须包含 2-5 个高层步骤；\n"
    "二、每个步骤包含 description（做什么）、goal（为什么做）、done_when（完成判定）、phase（research/synthesis/implementation/verification）；\n"
    "三、计划粒度到步骤，不在计划中生成具体 tool 或 args；\n"
    "四、如果 leader 提供了反馈或修改意见，必须据此重新生成完整计划，而不是增量修订；\n"
    "五、若 leader 在任务描述中提到之前的相关文件，应先读取或确认后再规划。\n"
    "输出格式：严格按 V4 输出协议，使用 type=text，content 为计划 JSON：\n"
    '{"tasks":[{"description":"步骤描述","goal":"目标","done_when":"完成判定","phase":"research|synthesis|implementation|verification"}]}'
)


class PlanDefinition(AgentDefinition):
    """Plan Agent 定义（V4：作为子代理工具 call_plan_agent 执行）。

    每次由 leader 调用时重新生成计划（不做增量修订）；计划文件由
    call_plan_agent 工具执行器在收到 text 输出后写入 plan.md。
    """

    def __init__(self):
        try:
            from singleton import get_settings_service
            _settings = get_settings_service()
            _max_iter = int(_settings.get("agent:iterations:director:max"))
            _timeout = int(_settings.get("agent:subagent_timeout_seconds"))
        except (KeyError, ValueError, ImportError):
            _max_iter = 8
            _timeout = 1800
        super().__init__(
            prompt=AgentPrompt(
                system_prompt=PLAN_AGENT_PROMPT,
                mode="DIRECT",
            ),
            meta=AgentMeta(
                allowed_tools=[
                    "list_workspace_files",
                    "get_workspace_info",
                    "search_files",
                    "read_file",
                ],
                default_tools=[],
                timeout_seconds=_timeout,
                max_iterations=min(_max_iter, 8),
                memory_mode="accumulate",
                agent_type="plan_agent",
                is_subagent=True,
            ),
        )
