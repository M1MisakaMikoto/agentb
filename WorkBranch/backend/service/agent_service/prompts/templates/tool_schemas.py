"""
工具Schema管理器
提供工具列表的生成和格式化功能
"""

from typing import List, Optional


class ToolSchemaManager:
    """工具Schema管理器"""

    # (agent_type, issues_tuple) -> 已告警过，避免每轮重复刷屏
    _reported: set = set()

    @classmethod
    def build_tool_schema_prompt(cls, tool_names: List[str]) -> str:
        """
        构建工具列表提示词
        
        Args:
            tool_names: 工具名称列表
        
        Returns:
            格式化的工具列表字符串
        """
        if not tool_names:
            return ""
        
        try:
            from service.agent_service.tools import ALL_TOOLS
            schema_lines = ["工具列表："]
            
            for tool_name in tool_names:
                tool_meta = ALL_TOOLS.get(tool_name)
                if not tool_meta:
                    continue
                
                params = tool_meta.get("params", "")
                if params:
                    schema_lines.append(params)
            
            return "\n".join(schema_lines) if len(schema_lines) > 1 else ""
            
        except Exception as e:
            print(f"[ToolSchemaManager] 构建工具Schema失败: {e}")
            return f"工具列表：{', '.join(tool_names)}"
    
    @classmethod
    def get_allowed_tools(cls, agent_type: str, settings_service=None) -> List[str]:
        """
        获取指定Agent类型允许使用的工具
        
        Args:
            agent_type: agent类型
            settings_service: 设置服务
        
        Returns:
            工具名称列表
        """
        try:
            from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools as _get_allowed
            return _get_allowed(agent_type, settings_service)
        except Exception as e:
            print(f"[ToolSchemaManager] 获取允许工具失败: {e}")
            return []

    @classmethod
    def validate_consistency(cls, agent_type: str, settings_service=None) -> List[str]:
        """
        校验 AgentDefinition 允许的工具与工具注册表(ALL_TOOLS)的一致性。

        Returns:
            问题列表（空表示一致）。
        """
        try:
            from service.agent_service.tools import ALL_TOOLS
            from service.agent_service.graph.subgraphs.tool_registry import get_allowed_tools
        except Exception:
            return []

        issues: List[str] = []
        allowed_tools = get_allowed_tools(agent_type, settings_service)
        registered = set(ALL_TOOLS.keys())

        for name in allowed_tools:
            if name not in registered:
                issues.append(
                    f"allowed tool '{name}' 未注册到 ALL_TOOLS：提示词中缺失、调用时无法执行"
                )
            else:
                meta = ALL_TOOLS[name]
                if not meta.get("params"):
                    issues.append(
                        f"tool '{name}' 缺少 params：模型无法获知调用格式"
                    )

        return issues

    @classmethod
    def validate_and_report(cls, agent_type: str, settings_service=None) -> None:
        """一致性校验并去重告警（不改变行为）。"""
        try:
            issues = cls.validate_consistency(agent_type, settings_service)
            if not issues:
                return
            key = (agent_type, tuple(issues))
            if key in cls._reported:
                return
            cls._reported.add(key)
            print(f"[ToolSchemaManager] 工具一致性告警 ({agent_type}):")
            for issue in issues:
                print(f"  - {issue}")
        except Exception:
            pass
