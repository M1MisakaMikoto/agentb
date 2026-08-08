from dataclasses import dataclass
from typing import Dict
from .registry import ToolDefinition, ToolRegistry


@dataclass
class PlanModeConfig:
    """Plan 模式配置"""
    max_steps: int = 5
    require_approval: bool = True
    auto_execute: bool = False


PLAN_TOOLS: Dict[str, ToolDefinition] = {}


def register_plan_tools():
    """注册规划工具"""
    registry = ToolRegistry()
    for tool_name, tool_def in PLAN_TOOLS.items():
        registry.register(tool_def)
