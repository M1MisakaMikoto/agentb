"""
工具Schema管理器
提供工具列表的生成和格式化功能
"""

from typing import List, Optional


class ToolSchemaManager:
    """工具Schema管理器"""
    
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
