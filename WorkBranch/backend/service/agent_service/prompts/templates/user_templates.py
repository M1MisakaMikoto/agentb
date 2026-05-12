"""
User Message模板管理器
提供结构化的User Message模板
"""

from typing import List, Optional


class UserTemplateManager:
    """User Message模板管理器"""
    
    # 静态规则说明（相对固定，放在工具列表后面）
    STATIC_RULES = (
        "注意：只有当 todo 列表非空时，你才应围绕 todo 执行；"
        "如果当前没有 todo 且任务明显多步骤/阶段化，可以先使用 update_todo 写入完整 todo 列表。"
        "如果 todo 列表非空，你应继续通过 update_todo 覆盖更新完整 todo 列表和 doingIdx；"
        "如果任务拆分发生变化，也应通过 update_todo 一次性重写。"
        "默认按 DIRECT 执行；如果你在执行过程中发现任务明显复杂、多阶段、跨文件、需要先输出方案，"
        "才调用 switch_execution_mode 把模式切到 PLAN。"
        "如果上一条历史对话提到了 plan.md，并且当前用户消息表达了批准/继续执行方案的语义，"
        "那么你应先使用 read_file 读取该 plan.md，再严格遵守该计划执行。"
        "除非用户明确要求查看计划文件，否则不要为了展示而读取 plan.md。\n\n"
        
        "请只决定下一步动作，并以 JSON 形式返回："
        "如果需要继续操作，返回一个 tool 调用；"
        "如果当前 todo 已完成，返回 kind=step_done；"
        "如果需要向用户输出最终回复，使用 chat 工具；"
        "如果无法继续，返回 kind=blocked。"
    )
    
    @classmethod
    def build_static_section(cls, tool_schema_prompt: str) -> str:
        """
        构建静态区域（高缓存命中）
        
        包含：
        1. 工具列表（几乎不变）
        2. 规则说明（相对静态）
        """
        if not tool_schema_prompt:
            return ""
        
        parts = [tool_schema_prompt, cls.STATIC_RULES]
        return "\n".join(parts)
    
    @classmethod
    def build_plan_mode_suffix(cls) -> str:
        """构建PLAN模式的额外指令"""
        return (
            "请只决定下一步动作，并以 JSON 形式返回："
            "如果需要继续操作，返回一个 tool 调用；"
            "如果计划已完成，返回 kind=step_done；"
            "如果需要向用户输出回复，使用 chat 工具；"
            "如果无法继续，返回 kind=blocked。"
        )
    
    @classmethod
    def format_current_question(cls, user_message: str) -> str:
        """格式化当前问题"""
        return f"\n## 当前问题\n\n**用户**: {user_message}\n"
