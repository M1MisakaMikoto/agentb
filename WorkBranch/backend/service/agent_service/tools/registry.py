from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    params: str
    category: str = "general"
    executor: Optional[Callable] = None


class ToolRegistry:
    """工具注册表"""

    _instance = None
    _tools: Dict[str, ToolDefinition] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, tool: ToolDefinition) -> None:
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Optional[ToolDefinition]:
        return cls._tools.get(name)

    @classmethod
    def get_all(cls) -> Dict[str, ToolDefinition]:
        return cls._tools.copy()

    @classmethod
    def get_by_category(cls, category: str) -> List[ToolDefinition]:
        return [t for t in cls._tools.values() if t.category == category]

    @classmethod
    def get_tool_prompt(cls, allowed_tools: List[str]) -> str:
        if not allowed_tools:
            return "当前没有可用工具。"

        lines = ["## 工具列表"]
        for name in allowed_tools:
            tool = cls._tools.get(name)
            if tool and tool.params:
                lines.append(tool.params)

        return "\n".join(lines)


ALL_TOOLS = {
    "read_file": {
        "name": "read_file",
        "description": "读取文件内容",
        "params": 'read_file:{"file_path":"(文件路径)","start_line":"(第几行开始读，本参数可不填)","end_line":"(第几行结束读，本参数可不填)"}'
    },
    "write_file": {
        "name": "write_file",
        "description": "写入文件",
        "params": 'write_file:{"file_path":"(文件路径)","content":"(写入内容)","mode":"(write或append，本参数可不填)"}'
    },
    "delete_file": {
        "name": "delete_file",
        "description": "删除文件或目录",
        "params": 'delete_file:{"file_path":"(文件路径)"}'
    },
    "list_dir": {
        "name": "list_dir",
        "description": "列出目录内容",
        "params": 'list_dir:{"directory":"(目录路径，本参数可不填)","recursive":"(是否递归，本参数可不填)","show_hidden":"(是否显示隐藏文件，本参数可不填)"}'
    },
    "create_dir": {
        "name": "create_dir",
        "description": "创建目录",
        "params": 'create_dir:{"directory":"(目录路径)"}'
    },
    "explore_code": {
        "name": "explore_code",
        "description": "探索代码库",
        "params": 'explore_code:{"query":"(查询内容)","search_type":"(file/code/structure，本参数可不填)","file_pattern":"(文件匹配模式，本参数可不填)","max_results":"(最多返回多少条，本参数可不填)"}'
    },
    "explore_internet": {
        "name": "explore_internet",
        "description": "搜索互联网获取信息",
        "params": 'explore_internet:{"query":"(搜索内容)","max_results":"(最多返回多少条，本参数可不填)"}'
    },
    "thinking": {
        "name": "thinking",
        "description": "思考工具",
        "params": 'thinking:{}'
    },
    "chat": {
        "name": "chat",
        "description": "与用户对话工具",
        "params": 'chat:{}'
    },
    "call_explore_agent": {
        "name": "call_explore_agent",
        "description": "调用探索子代理",
        "params": 'call_explore_agent:{"task_description":"(交给探索子代理的任务描述)"}'
    },
    "call_review_agent": {
        "name": "call_review_agent",
        "description": "调用审查子代理",
        "params": 'call_review_agent:{"task_description":"(交给审查子代理的任务描述)"}'
    },
    "update_todo": {
        "name": "update_todo",
        "description": "用完整列表覆盖更新 TODO 状态",
        "params": 'update_todo:{"todos": ["(todo内容1)", "(todo内容2)"...],"doingIdx": (当前todo进行到第几项了，从0开始数)}'
    },
    "switch_execution_mode": {
        "name": "switch_execution_mode",
        "description": "切换当前执行模式",
        "params": 'switch_execution_mode:{"mode":"PLAN","reason":"(为什么需要切到PLAN)"}'
    },
    "rag_search": {
        "name": "rag_search",
        "description": "在知识库中进行语义检索",
        "params": 'rag_search:{"query":"(查询内容)","kb_ids":"(知识库ID列表，本参数可不填)","top_k":"(返回条数，本参数可不填)","min_score":"(最低相关度，本参数可不填)"}'
    },
    "list_workspace_files": {
        "name": "list_workspace_files",
        "description": "列出当前工作区内所有文件和目录",
        "params": 'list_workspace_files:{}'
    },
    "get_workspace_info": {
        "name": "get_workspace_info",
        "description": "获取当前工作区信息",
        "params": 'get_workspace_info:{}'
    },
    "search_files": {
        "name": "search_files",
        "description": "在工作区内搜索文件",
        "params": 'search_files:{"pattern":"(文件名模式，支持通配符*)"}'
    }
}


FILE_TOOLS = {"read_file", "write_file", "delete_file", "list_dir", "create_dir"}
EXPLORE_TOOLS = {"explore_code", "explore_internet"}
SUBAGENT_TOOLS = {"call_explore_agent", "call_review_agent"}
TODO_TOOLS = {"update_todo"}
RAG_TOOLS = {"rag_search"}
WORKSPACE_TOOLS = {"list_workspace_files", "get_workspace_info", "search_files"}
