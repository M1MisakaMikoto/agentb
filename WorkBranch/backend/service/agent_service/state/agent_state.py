from typing import TypedDict, List, Any, Optional, Literal
from enum import Enum


class AgentType(str, Enum):
    """Agent 类型枚举"""
    PLAN_AGENT = "plan_agent"
    DIRECTOR_AGENT = "director_agent"
    REVIEW_AGENT = "review_agent"
    EXPLORE_AGENT = "explore_agent"
    ADMIN_AGENT = "admin_agent"


class IntentType(str, Enum):
    """用户意图类型"""
    DEVELOP = "develop"
    EXPLORE = "explore"
    REVIEW = "review"
    QUESTION = "question"
    DEBUG = "debug"
    REFACTOR = "refactor"
    OTHER = "other"


class TaskPhase(str, Enum):
    """任务阶段"""
    RESEARCH = "research"
    SYNTHESIS = "synthesis"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(TypedDict, total=False):
    """单个任务定义"""
    id: int
    description: str
    goal: Optional[str]
    done_when: Optional[str]
    phase: str
    status: str
    tool: Optional[str]
    args: Optional[dict]
    result: Optional[str]
    feedback: Optional[str]


class ToolCall(TypedDict):
    """工具调用记录"""
    tool: str
    args: dict
    result: Optional[str]


class IntentAnalysis(TypedDict):
    """意图分析结果"""
    intent_type: str
    summary: str
    key_points: List[str]
    suggested_tools: List[str]
    complexity: str
    confidence: float


class NextAction(TypedDict, total=False):
    kind: Literal["tool", "reply", "step_done", "blocked"]
    tool_name: Optional[str]
    tool_args: Optional[dict]
    reply: Optional[str]
    task_description: Optional[str]


class TodoItem(TypedDict, total=False):
    id: int
    description: str
    goal: Optional[str]
    done_when: Optional[str]
    status: str
    result: Optional[str]
    attempt_count: Optional[int]


class AgentState(TypedDict):
    """Agent 状态定义"""
    messages: List[Any]
    workspace_id: str
    plan: List[Task]
    current_step: int
    results: List[Any]
    plan_failed: bool
    explore_result: Optional[dict]
    tool_history: List[ToolCall]
    replan_count: int
    agent_type: Optional[str]
    is_root_graph: Optional[bool]
    intent_analysis: Optional[IntentAnalysis]
    parent_chain_messages: Optional[List[dict]]
    current_conversation_messages: Optional[List[dict]]
    execution_mode: Optional[str]
    mode_reason: Optional[str]
    suggested_tools: Optional[List[str]]
    in_plan_mode: Optional[bool]
    pending_tools: Optional[List[dict]]
    has_tool_use: Optional[bool]
    final_reply: Optional[str]
    plan_file: Optional[str]
    last_tool_result: Optional[str]
    last_tool_name: Optional[str]
    last_tool_success: Optional[bool]
    last_tool_error: Optional[str]
    iteration_count: Optional[int]
    max_iterations: Optional[int]
    current_step_goal: Optional[str]
    current_step_done_when: Optional[str]
    current_step_iteration_count: Optional[int]
    step_max_iterations: Optional[int]
    step_status: Optional[str]
    replan_reason: Optional[str]
    todos: Optional[List[TodoItem]]
    current_todo_index: Optional[int]
    current_todo_goal: Optional[str]
    current_todo_done_when: Optional[str]
    current_todo_iteration_count: Optional[int]
    todo_max_iterations: Optional[int]
    todo_status: Optional[str]
    next_action: Optional[NextAction]
