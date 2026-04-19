from .registry import ToolRegistry, ToolDefinition, ALL_TOOLS, FILE_TOOLS, EXPLORE_TOOLS, SUBAGENT_TOOLS, RAG_TOOLS, WORKSPACE_TOOLS, DOCUMENT_TOOLS, SQL_TOOLS
from .plan_tools import register_plan_tools, PLAN_TOOLS
from .agent_tools import register_agent_tools, AGENT_TOOLS
from .rag_tool import register_rag_tools
from .document_tools import register_document_tools, DOCUMENT_TOOLS as DOC_TOOLS
from .sql_tools import register_sql_tools, SQL_TOOLS as SQL_TOOLS_DEF
from .executors import ToolExecutor

__all__ = [
    "ToolRegistry",
    "ToolDefinition",
    "ToolExecutor",
    "ALL_TOOLS",
    "FILE_TOOLS",
    "EXPLORE_TOOLS",
    "SUBAGENT_TOOLS",
    "PLAN_TOOLS",
    "AGENT_TOOLS",
    "RAG_TOOLS",
    "WORKSPACE_TOOLS",
    "DOCUMENT_TOOLS",
    "SQL_TOOLS",
    "register_plan_tools",
    "register_agent_tools",
    "register_rag_tools",
    "register_document_tools",
    "register_sql_tools",
]

def register_all_tools():
    """注册所有工具"""
    register_plan_tools()
    register_agent_tools()
    register_rag_tools()
    register_document_tools()
    register_sql_tools()

