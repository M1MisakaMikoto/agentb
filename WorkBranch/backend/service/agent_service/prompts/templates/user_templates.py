"""
User Message模板管理器
提供结构化的User Message模板
"""

from typing import List, Optional


class UserTemplateManager:
    """User Message模板管理器"""
    
    # 静态规则说明（已迁移至System Prompt）
    # 仅保留简短的输出格式提醒
    STATIC_RULES = (
        "请只决定下一步动作，并以 JSON 形式返回；顶层必须是单个 JSON 对象，禁止返回数组或其他类型："
        "如果需要继续操作，返回一个 tool 调用；"
        "如果当前 todo 已完成，返回 kind=step_done；"
        "如果需要向用户输出最终回复，使用 chat 工具；"
        "如果无法继续，返回 kind=blocked。"
    )
    
    @classmethod
    def build_static_section(cls, tool_schema_prompt: str = "") -> str:
        """
        构建静态区域（高缓存命中）
        
        只包含规则说明（工具列表已在System Prompt中）
        """
        return cls.STATIC_RULES
    
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
