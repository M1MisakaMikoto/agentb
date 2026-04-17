"""
Director Agent - 统一编排图

完全合并工具执行逻辑，确保上下文正确传递。

参考 Claude Code 架构：
1. 循环判断：使用块类型（tool_use/chat）驱动，而非状态机
2. Plan 模式：生成计划写入文件，输出给用户，graph 结束
3. Execute 模式：按步骤执行
4. 最终回复：chat 工具输出，打破循环
"""
from typing import Literal, Optional, Dict, Any, List, Callable
from langgraph.graph import StateGraph, END
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import os
import shutil
import fnmatch

from .decision.complexity_analyzer import ExecutionMode, analyze_task_complexity, evaluate_task_complexity
from ..state import AgentState
from .subgraphs.tool_registry import (
    FILE_TOOLS, EXPLORE_TOOLS, SUBAGENT_TOOLS, WORKSPACE_TOOLS, SPECIAL_TOOLS,
    is_tool_allowed, get_allowed_tools, _write_tool_event
)
from service.session_service.canonical import SegmentType
from service.agent_service.service.plan_file_service import plan_file_service
from service.agent_service.service.workspace_service import WorkspaceService
from core.logging import console

MAX_REPLAN_COUNT = 3
MAX_MESSAGES = 10

workspace_service = WorkspaceService()

THINK_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前正在执行一个任务计划中的某个步骤。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请针对当前任务进行思考：
1. 分析任务目标
2. 结合之前的执行结果（如果有）
3. 给出你的思考过程和结论

请简洁清晰地回答，不要过于冗长。"""

CHAT_SYSTEM_PROMPT = """你是一个专业的软件工程师助手。当前需要向用户输出回复。

你会收到：
1. 当前任务描述
2. 之前任务的执行结果（如果有）

请直接向用户输出回复内容：
- 语言简洁清晰
- 直接回答用户问题
- 不要输出思考过程，只输出最终回复
- 使用友好、专业的语气"""


def build_context_prompt(
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
    current_task: str
) -> str:
    prompt_parts = []
    
    if parent_chain_messages:
        prompt_parts.append("[历史对话]")
        for msg in parent_chain_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")
        prompt_parts.append("")
    
    if current_conversation_messages:
        prompt_parts.append("[当前对话内历史]")
        for msg in current_conversation_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")
        prompt_parts.append("")
    
    prompt_parts.append("[当前任务]")
    prompt_parts.append(current_task)
    
    return "\n".join(prompt_parts)


def build_initial_state(
    user_message: str,
    workspace_id: str,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None,
    agent_type: Optional[str] = None,
    is_root_graph: bool = False,
) -> dict:
    return {
        "messages": [user_message],
        "workspace_id": workspace_id,
        "plan": [],
        "current_step": 0,
        "results": [],
        "plan_failed": False,
        "explore_result": None,
        "tool_history": [],
        "replan_count": 0,
        "agent_type": agent_type,
        "is_root_graph": is_root_graph,
        "parent_chain_messages": parent_chain_messages or [],
        "current_conversation_messages": current_conversation_messages or [],
        "has_tool_use": False,
        "final_reply": None,
        "plan_file": None
    }


def check_state_v3(state: AgentState) -> Literal["analyze", "execute", "plan", "subagent", "done"]:
    if "execution_mode" not in state:
        return "analyze"
    
    if state.get("execution_mode") is None:
        return "done"
    
    if state.get("execution_mode") == ExecutionMode.PLAN:
        return "plan"
    
    if state.get("active_subagent"):
        return "subagent"
    
    if state.get("has_tool_use", False):
        return "execute"
    
    if state.get("pending_tools"):
        return "execute"
    
    if state.get("final_reply"):
        return "done"
    
    return "done"


def route_after_analyze(state: dict) -> str:
    mode = state.get("execution_mode")
    if mode == ExecutionMode.PLAN:
        return "plan"
    elif mode == ExecutionMode.SUBAGENT:
        return "subagent"
    elif mode == ExecutionMode.DIRECT:
        return "execute"
    return "done"


def create_analyze_node(llm_service=None, message_context=None, settings_service=None):
    def analyze_node(state: AgentState) -> dict:
        user_message = state["messages"][-1] if state["messages"] else ""
        current_agent_type = state.get("agent_type") or "director_agent"

        console.step("分析节点", "入口", user_message)
        
        if llm_service:
            system_prompt = """你是一个任务分析专家。请分析用户任务的复杂度，并决定执行模式。

执行模式选项：
1. DIRECT - 直接执行：适用于简单任务，如读取文件、查询信息等
2. PLAN - 规划模式：适用于复杂开发任务，需要多步骤规划（仅 director_agent 可用）
3. SUBAGENT - 子Agent模式：适用于特定类型任务，如探索、审查等（仅 director_agent 可用）

请以JSON格式返回分析结果：
{
    "complexity": "simple/medium/complex",
    "intent_type": "develop/explore/review/question/debug/refactor/other",
    "execution_mode": "DIRECT/PLAN/SUBAGENT",
    "reason": "选择该模式的原因",
    "suggested_agent": "explore/review/None"
}

只返回JSON，不要其他内容。"""
            
            parent_chain_messages = state.get("parent_chain_messages", [])
            current_conversation_messages = state.get("current_conversation_messages", [])
            
            full_prompt = build_context_prompt(
                parent_chain_messages,
                current_conversation_messages,
                f"user: {user_message}"
            )
            messages = [{"role": "user", "content": full_prompt}]
            
            try:
                response = llm_service.chat(messages, system_prompt=system_prompt)
                
                console.response_box(response)
                
                import json
                response_text = response.strip()
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                if response_text.startswith("```"):
                    response_text = response_text[3:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
                response_text = response_text.strip()
                
                analysis_result = json.loads(response_text)
                
                mode_str = analysis_result.get("execution_mode", "DIRECT")
                execution_mode = ExecutionMode[mode_str]
                
                mode_decision = {
                    "mode": execution_mode,
                    "reason": analysis_result.get("reason", ""),
                    "suggested_agent": analysis_result.get("suggested_agent")
                }

                if current_agent_type != "director_agent":
                    mode_decision["mode"] = ExecutionMode.DIRECT
                    mode_decision["suggested_agent"] = None
                    mode_decision["reason"] = f"{current_agent_type} 使用专属 graph，固定走 DIRECT 执行"

                intent_analysis = {
                    "intent_type": analysis_result.get("intent_type", "other"),
                    "summary": user_message[:100],
                    "key_points": [user_message],
                    "complexity": analysis_result.get("complexity", "medium"),
                    "confidence": 0.9
                }
                
            except Exception as e:
                console.warning(f"调用大模型失败: {e}，使用默认逻辑")
                complexity = evaluate_task_complexity(user_message)
                intent_analysis = {
                    "intent_type": "other",
                    "summary": user_message[:100],
                    "key_points": [user_message],
                    "complexity": complexity,
                    "confidence": 0.7
                }
                mode_decision = analyze_task_complexity(user_message, intent_analysis)
        else:
            complexity = evaluate_task_complexity(user_message)
            intent_analysis = {
                "intent_type": "other",
                "summary": user_message[:100],
                "key_points": [user_message],
                "complexity": complexity,
                "confidence": 0.7
            }
            mode_decision = analyze_task_complexity(user_message, intent_analysis)
        
        console.decision_box(
            route_after_analyze({'execution_mode': mode_decision['mode']}),
            f"执行模式: {mode_decision['mode']}\n原因: {mode_decision['reason']}"
        )
        
        result = {
            "intent_analysis": intent_analysis,
            "execution_mode": mode_decision["mode"],
            "mode_reason": mode_decision["reason"],
            "suggested_tools": [],
            "suggested_subagent": mode_decision["suggested_agent"],
            "active_subagent": mode_decision["mode"] == ExecutionMode.SUBAGENT,
            "has_tool_use": False,
            "final_reply": None
        }

        if mode_decision["mode"] == ExecutionMode.DIRECT and current_agent_type == "director_agent":
            result["pending_tools"] = [
                {"tool": "thinking", "args": {"description": user_message}},
                {"tool": "chat", "args": {"description": user_message}},
            ]
        elif mode_decision["mode"] == ExecutionMode.DIRECT:
            suggested_tools = []
            if current_agent_type == "explore_agent":
                suggested_tools = ["thinking", "chat"]
            elif current_agent_type == "review_agent":
                suggested_tools = ["thinking", "chat"]
            result["pending_tools"] = [
                {"tool": tool, "args": {"description": user_message}}
                for tool in suggested_tools
            ]
        
        if message_context:
            send_message = message_context.get("send_message")
            if send_message:
                from service.session_service.canonical import MessageBuilder
                state_metadata = {
                    "execution_mode": mode_decision["mode"].name,
                    "active_subagent": result.get("active_subagent")
                }
                state_msg = MessageBuilder.state_change(
                    message_id=message_context.get("message_id", ""),
                    conversation_id=message_context.get("conversation_id", ""),
                    session_id=message_context.get("session_id", ""),
                    workspace_id=message_context.get("workspace_id", ""),
                    metadata=state_metadata
                )
                send_message("", SegmentType.STATE_CHANGE, state_metadata)
        
        return result
    
    return analyze_node


def create_plan_node(llm_service=None, token_callback=None, settings_service=None, message_context=None):
    def plan_node(state: AgentState) -> dict:
        user_message = state["messages"][-1] if state["messages"] else ""
        workspace_id = state["workspace_id"]
        
        console.step("规划节点", "分析节点", user_message)
        
        workspace_info = workspace_service.get_workspace_info(workspace_id)
        session_id = workspace_info.get("session_id", "default") if workspace_info else "default"
        
        if llm_service:
            from .subgraphs.plan_graph import get_plan_system_prompt, parse_plan_from_text
            
            system_prompt = get_plan_system_prompt("director_agent", settings_service)
            
            messages = [{"role": "user", "content": f"请为以下任务生成详细的执行计划，包含2-5个步骤：\n\n{user_message}"}]
            
            try:
                response = llm_service.chat(messages, system_prompt=system_prompt)
                
                console.response_box(response)
                
                plan = parse_plan_from_text(response)
                
                for i, task in enumerate(plan):
                    task["id"] = i + 1
                
                console.task_list_box(plan)
                
            except Exception as e:
                console.warning(f"调用大模型失败: {e}，使用默认计划")
                plan = [
                    {"id": 1, "description": f"分析需求: {user_message[:30]}...", "phase": "research", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                    {"id": 2, "description": "设计实现方案", "phase": "synthesis", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                    {"id": 3, "description": "执行实现", "phase": "implementation", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                    {"id": 4, "description": "验证结果", "phase": "verification", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                ]
        else:
            console.warning("LLM服务未配置，使用默认计划")
            plan = [
                {"id": 1, "description": f"分析需求: {user_message[:30]}...", "phase": "research", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                {"id": 2, "description": "设计实现方案", "phase": "synthesis", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                {"id": 3, "description": "执行实现", "phase": "implementation", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
                {"id": 4, "description": "验证结果", "phase": "verification", "status": "pending", "tool": None, "args": None, "result": None, "feedback": None},
            ]
        
        plan_content = plan_file_service.format_plan_as_markdown(user_message, plan)
        
        create_result = plan_file_service.create_plan(
            session_id=session_id,
            workspace_id=workspace_id,
            plan_content=plan_content,
            plan_steps=plan,
            metadata={"task_description": user_message}
        )
        
        console.box("计划文件已创建", create_result.get("plan_file"))
        
        if message_context:
            send_message = message_context.get("send_message")
            if send_message:
                from service.session_service.canonical import MessageBuilder
                
                state_metadata = {
                    "execution_mode": "PLAN",
                    "plan_steps": len(plan),
                    "plan_file": create_result.get("plan_file")
                }
                send_message("", SegmentType.STATE_CHANGE, state_metadata)
                send_message(plan_content, SegmentType.TEXT_DELTA)
        
        console.decision_box("done", "计划已生成，等待用户确认执行")
        
        return {
            "plan": plan,
            "plan_file": create_result.get("plan_file"),
            "final_reply": plan_content
        }
    
    return plan_node


def _format_file_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _execute_read_file(tool_args: dict) -> dict:
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    encoding = tool_args.get("encoding", "utf-8")
    start_line = tool_args.get("start_line", 1)
    end_line = tool_args.get("end_line")
    
    console.info(f"read_file: {file_path}")
    
    try:
        if not os.path.exists(file_path):
            return {"result": None, "error": f"文件不存在: {file_path}"}
        
        if not os.path.isfile(file_path):
            return {"result": None, "error": f"路径不是文件: {file_path}"}
        
        with open(file_path, "r", encoding=encoding) as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        start_idx = max(0, start_line - 1)
        end_idx = end_line if end_line else total_lines
        
        selected_lines = lines[start_idx:end_idx]
        
        result_lines = []
        for i, line in enumerate(selected_lines, start=start_idx + 1):
            result_lines.append(f"{i:6d}\t{line.rstrip()}")
        
        content = "\n".join(result_lines)
        if end_line is None or end_line >= total_lines:
            summary = f"文件共 {total_lines} 行，已读取全部内容"
        else:
            summary = f"文件共 {total_lines} 行，已读取第 {start_line}-{end_line} 行"
        
        console.success(f"read_file 成功: {summary}")
        return {"result": f"{summary}\n\n{content}", "error": None}
    
    except UnicodeDecodeError:
        return {"result": None, "error": f"文件编码错误，无法用 {encoding} 解码"}
    except Exception as e:
        console.error(f"read_file 失败: {e}")
        return {"result": None, "error": f"读取文件失败: {str(e)}"}


def _execute_write_file(tool_args: dict) -> dict:
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    content = tool_args.get("content")
    if content is None:
        return {"result": None, "error": "缺少 content 参数"}
    
    mode = tool_args.get("mode", "write")
    encoding = tool_args.get("encoding", "utf-8")
    
    console.info(f"write_file: {file_path}, mode: {mode}")
    
    try:
        dir_path = os.path.dirname(file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        
        write_mode = "a" if mode == "append" else "w"
        with open(file_path, write_mode, encoding=encoding) as f:
            f.write(content)
        
        action = "追加" if mode == "append" else "写入"
        console.success(f"write_file 成功: {action} {len(content)} 字符")
        return {"result": f"文件{action}成功: {file_path}", "error": None}
    
    except Exception as e:
        console.error(f"write_file 失败: {e}")
        return {"result": None, "error": f"写入文件失败: {str(e)}"}


def _execute_delete_file(tool_args: dict) -> dict:
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    console.info(f"delete_file: {file_path}")
    
    try:
        if not os.path.exists(file_path):
            return {"result": None, "error": f"路径不存在: {file_path}"}
        
        if os.path.isfile(file_path):
            os.remove(file_path)
            console.success("delete_file 成功: 已删除文件")
            return {"result": f"文件已删除: {file_path}", "error": None}
        elif os.path.isdir(file_path):
            shutil.rmtree(file_path)
            console.success("delete_file 成功: 已删除目录及其内容")
            return {"result": f"目录已删除: {file_path}", "error": None}
        else:
            return {"result": None, "error": f"未知文件类型: {file_path}"}
    
    except Exception as e:
        print(f"[ToolExec] delete_file 失败: {e}")
        return {"result": None, "error": f"删除失败: {str(e)}"}


def _execute_list_dir(tool_args: dict) -> dict:
    dir_path = tool_args.get("directory") or tool_args.get("path") or tool_args.get("dir_path")
    if not dir_path:
        return {"result": None, "error": "缺少 directory 参数"}
    
    recursive = tool_args.get("recursive", False)
    show_hidden = tool_args.get("show_hidden", False)
    
    print(f"[ToolExec] list_dir: {dir_path}, recursive: {recursive}")
    
    try:
        if not os.path.exists(dir_path):
            return {"result": None, "error": f"目录不存在: {dir_path}"}
        
        if not os.path.isdir(dir_path):
            return {"result": None, "error": f"路径不是目录: {dir_path}"}
        
        result_lines = []
        file_count = 0
        dir_count = 0
        
        if recursive:
            for root, dirs, files in os.walk(dir_path):
                if not show_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files = [f for f in files if not f.startswith(".")]
                
                rel_root = os.path.relpath(root, dir_path)
                if rel_root == ".":
                    rel_root = ""
                
                for d in sorted(dirs):
                    dir_count += 1
                    prefix = f"{rel_root}/" if rel_root else ""
                    result_lines.append(f"📁 {prefix}{d}/")
                
                for f in sorted(files):
                    file_count += 1
                    prefix = f"{rel_root}/" if rel_root else ""
                    result_lines.append(f"📄 {prefix}{f}")
        else:
            entries = os.listdir(dir_path)
            if not show_hidden:
                entries = [e for e in entries if not e.startswith(".")]
            
            for entry in sorted(entries):
                full_path = os.path.join(dir_path, entry)
                if os.path.isdir(full_path):
                    dir_count += 1
                    result_lines.append(f"📁 {entry}/")
                else:
                    file_count += 1
                    result_lines.append(f"📄 {entry}")
        
        summary = f"目录: {dir_path}\n共 {dir_count} 个目录, {file_count} 个文件"
        content = "\n".join(result_lines) if result_lines else "(空目录)"
        
        print(f"[ToolExec] list_dir 成功: {dir_count} 目录, {file_count} 文件")
        return {"result": f"{summary}\n\n{content}", "error": None}
    
    except Exception as e:
        print(f"[ToolExec] list_dir 失败: {e}")
        return {"result": None, "error": f"列出目录失败: {str(e)}"}


def _execute_create_dir(tool_args: dict) -> dict:
    dir_path = tool_args.get("directory") or tool_args.get("path") or tool_args.get("dir_path")
    if not dir_path:
        return {"result": None, "error": "缺少 directory 参数"}
    
    print(f"[ToolExec] create_dir: {dir_path}")
    
    try:
        if os.path.exists(dir_path):
            if os.path.isdir(dir_path):
                return {"result": f"目录已存在: {dir_path}", "error": None}
            else:
                return {"result": None, "error": f"路径已存在但不是目录: {dir_path}"}
        
        os.makedirs(dir_path, exist_ok=True)
        print(f"[ToolExec] create_dir 成功")
        return {"result": f"目录已创建: {dir_path}", "error": None}
    
    except Exception as e:
        print(f"[ToolExec] create_dir 失败: {e}")
        return {"result": None, "error": f"创建目录失败: {str(e)}"}


def _execute_explore_code(tool_args: dict) -> dict:
    import glob as glob_module
    import re
    
    workspace_root = tool_args.get("workspace_root", ".")
    query = tool_args.get("query", "")
    search_type = tool_args.get("search_type", "file")
    max_results = tool_args.get("max_results", 20)
    file_pattern = tool_args.get("file_pattern", "**/*.py")
    
    print(f"[ToolExec] explore_code: query={query}, type={search_type}")
    
    try:
        findings = []
        
        if search_type == "file":
            pattern = file_pattern if file_pattern else "**/*"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            for m in matches[:max_results]:
                if os.path.isfile(m):
                    rel_path = os.path.relpath(m, workspace_root)
                    findings.append({
                        "path": rel_path,
                        "type": "file",
                        "match": os.path.basename(m)
                    })
        
        elif search_type == "code":
            if not query:
                return {"result": None, "error": "code 搜索需要 query 参数"}
            
            pattern = file_pattern if file_pattern else "**/*.py"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            
            try:
                regex = re.compile(query, re.IGNORECASE)
            except re.error:
                regex = re.compile(re.escape(query), re.IGNORECASE)
            
            for file_path in matches:
                if not os.path.isfile(file_path):
                    continue
                if len(findings) >= max_results:
                    break
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                rel_path = os.path.relpath(file_path, workspace_root)
                                findings.append({
                                    "path": rel_path,
                                    "type": "code",
                                    "line": line_num,
                                    "content": line.strip()[:100]
                                })
                                if len(findings) >= max_results:
                                    break
                except Exception:
                    continue
        
        elif search_type == "structure":
            pattern = file_pattern if file_pattern else "**/*.py"
            matches = glob_module.glob(
                os.path.join(workspace_root, pattern),
                recursive=True
            )
            
            structure_patterns = {
                "class": re.compile(r"^\s*class\s+(\w+)"),
                "def": re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),
                "import": re.compile(r"^\s*(?:from|import)\s+([\w.]+)"),
            }
            
            for file_path in matches:
                if not os.path.isfile(file_path):
                    continue
                if len(findings) >= max_results:
                    break
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        rel_path = os.path.relpath(file_path, workspace_root)
                        file_structures = {"path": rel_path, "classes": [], "functions": [], "imports": []}
                        
                        for line in f:
                            for struct_type, pattern in structure_patterns.items():
                                match = pattern.match(line)
                                if match:
                                    file_structures[f"{struct_type}s"].append(match.group(1))
                        
                        if any([file_structures["classes"], file_structures["functions"], file_structures["imports"]]):
                            findings.append(file_structures)
                except Exception:
                    continue
        
        else:
            return {"result": None, "error": f"不支持的搜索类型: {search_type}"}
        
        if not findings:
            return {"result": "未找到匹配结果", "error": None}
        
        result_lines = [f"探索结果 (类型: {search_type}, 共 {len(findings)} 项):\n"]
        
        for item in findings:
            if item["type"] == "file":
                result_lines.append(f"  📄 {item['path']}")
            elif item["type"] == "code":
                result_lines.append(f"  📍 {item['path']}:{item['line']}")
                result_lines.append(f"     {item['content']}")
            elif "classes" in item:
                result_lines.append(f"  📁 {item['path']}")
                if item["classes"]:
                    result_lines.append(f"     Classes: {', '.join(item['classes'][:5])}")
                if item["functions"]:
                    result_lines.append(f"     Functions: {', '.join(item['functions'][:5])}")
        
        result = "\n".join(result_lines)
        print(f"[ToolExec] explore_code 成功: {len(findings)} 项结果")
        return {"result": result, "error": None}
    
    except Exception as e:
        print(f"[ToolExec] explore_code 失败: {e}")
        return {"result": None, "error": f"探索失败: {str(e)}"}


def _execute_explore_internet(tool_args: dict) -> dict:
    query = tool_args.get("query") or tool_args.get("description") or tool_args.get("task_description")
    if not query:
        return {"result": None, "error": "缺少 query 参数"}
    
    max_results = tool_args.get("max_results", 5)
    
    print(f"[ToolExec] explore_internet: {query}, max_results: {max_results}")
    
    try:
        from duckduckgo_search import DDGS
        
        results = []
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=max_results))
        
        if not search_results:
            return {"result": "未找到相关结果", "error": None}
        
        result_lines = [f"互联网搜索结果 (查询: {query}, 共 {len(search_results)} 项):\n"]
        
        for i, item in enumerate(search_results, 1):
            title = item.get("title", "无标题")
            href = item.get("href", "")
            body = item.get("body", "")
            
            result_lines.append(f"{i}. {title}")
            if href:
                result_lines.append(f"   链接: {href}")
            if body:
                truncated_body = body[:300] + "..." if len(body) > 300 else body
                result_lines.append(f"   摘要: {truncated_body}")
            result_lines.append("")
        
        result = "\n".join(result_lines)
        print(f"[ToolExec] explore_internet 成功: {len(search_results)} 项结果")
        return {"result": result, "error": None}
    
    except ImportError:
        error_msg = "duckduckgo-search 库未安装，请运行: pip install duckduckgo-search"
        print(f"[ToolExec] explore_internet 失败: {error_msg}")
        return {"result": None, "error": error_msg}
    
    except Exception as e:
        print(f"[ToolExec] explore_internet 失败: {e}")
        return {"result": None, "error": f"搜索失败: {str(e)}"}


def _execute_call_explore_agent(tool_args: dict, llm_service=None, token_callback=None, message_context: dict = None, parent_chain_messages: List[dict] = None, current_conversation_messages: List[dict] = None) -> dict:
    task_description = tool_args.get("task_description")
    if not task_description:
        return {"result": None, "error": "缺少 task_description 参数"}

    print(f"[ToolExec] call_explore_agent: {task_description}")

    if llm_service is None:
        return {"result": None, "error": "LLM 服务未配置，无法执行子代理任务"}

    workspace_id = None
    settings_service = None
    if message_context:
        workspace_id = message_context.get("workspace_id")
        settings_service = message_context.get("settings_service")

    if not workspace_id:
        return {"result": None, "error": "缺少 workspace_id，无法切换到探索 Agent Graph"}

    try:
        from .agent_graphs import run_agent_graph
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent_graph,
                "explore_agent",
                task_description,
                workspace_id,
                llm_service,
                token_callback,
                "accumulate",
                3,
                settings_service,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
                False,
            )
            try:
                outcome = future.result(timeout=45)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": "explore_agent 子图执行超时",
                        "details": {"agent_type": "explore_agent", "timeout_seconds": 45},
                    },
                }
        if outcome.get("status") == "failed":
            exit_info = outcome.get("exit_info") or {}
            error_msg = exit_info.get("message") or exit_info.get("code") or "子代理执行失败"
            print(f"[ToolExec] call_explore_agent 失败: {error_msg}")
            return {"result": None, "error": error_msg, "outcome": outcome}
        result = outcome.get("payload") or ""
        print(f"[ToolExec] call_explore_agent 完成")
        return {"result": result, "error": None, "outcome": outcome}

    except Exception as e:
        print(f"[ToolExec] call_explore_agent 失败: {e}")
        return {"result": None, "error": f"子代理执行失败: {str(e)}"}


def _execute_call_review_agent(tool_args: dict, llm_service=None, token_callback=None, message_context: dict = None, parent_chain_messages: List[dict] = None, current_conversation_messages: List[dict] = None) -> dict:
    task_description = tool_args.get("task_description")
    if not task_description:
        return {"result": None, "error": "缺少 task_description 参数"}

    print(f"[ToolExec] call_review_agent: {task_description}")

    if llm_service is None:
        return {"result": None, "error": "LLM 服务未配置，无法执行子代理任务"}

    workspace_id = None
    settings_service = None
    if message_context:
        workspace_id = message_context.get("workspace_id")
        settings_service = message_context.get("settings_service")

    if not workspace_id:
        return {"result": None, "error": "缺少 workspace_id，无法切换到审查 Agent Graph"}

    try:
        from .agent_graphs import run_agent_graph
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent_graph,
                "review_agent",
                task_description,
                workspace_id,
                llm_service,
                token_callback,
                "accumulate",
                3,
                settings_service,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
                False,
            )
            try:
                outcome = future.result(timeout=45)
            except FutureTimeoutError:
                future.cancel()
                outcome = {
                    "kind": "graph",
                    "status": "failed",
                    "payload": None,
                    "produced_user_reply": False,
                    "exit_info": {
                        "code": "subgraph_timeout",
                        "message": "review_agent 子图执行超时",
                        "details": {"agent_type": "review_agent", "timeout_seconds": 45},
                    },
                }
        if outcome.get("status") == "failed":
            exit_info = outcome.get("exit_info") or {}
            error_msg = exit_info.get("message") or exit_info.get("code") or "子代理执行失败"
            print(f"[ToolExec] call_review_agent 失败: {error_msg}")
            return {"result": None, "error": error_msg, "outcome": outcome}
        result = outcome.get("payload") or ""
        print(f"[ToolExec] call_review_agent 完成")
        return {"result": result, "error": None, "outcome": outcome}

    except Exception as e:
        print(f"[ToolExec] call_review_agent 失败: {e}")
        return {"result": None, "error": f"子代理执行失败: {str(e)}"}


def _execute_list_workspace_files(workspace_id: str, workspace_service) -> dict:
    console.info(f"列出工作区文件: {workspace_id}")
    
    success, files, error_msg = workspace_service.list_files(workspace_id)
    
    if not success:
        console.error(f"列出文件失败: {error_msg}")
        return {"result": None, "error": error_msg}
    
    if not files:
        console.success("工作区为空")
        return {"result": "工作区为空，暂无文件", "error": None}
    
    result_lines = ["工作区文件列表：\n"]
    for f in files:
        icon = "📁" if f["is_dir"] else "📄"
        size_str = "" if f["is_dir"] else f" ({_format_file_size(f['size'])})"
        result_lines.append(f"  {icon} {f['path']}{size_str}")
    
    result = "\n".join(result_lines)
    console.success(f"找到 {len(files)} 个文件/目录")
    return {"result": result, "error": None}


def _execute_get_workspace_info(workspace_id: str, workspace_service) -> dict:
    console.info(f"获取工作区信息: {workspace_id}")
    
    info = workspace_service.get_workspace_info(workspace_id)
    if not info:
        console.error(f"工作区不存在: {workspace_id}")
        return {"result": None, "error": f"工作区不存在: {workspace_id}"}
    
    workspace_dir = workspace_service.get_workspace_dir(workspace_id)
    
    result_lines = [
        "工作区信息：",
        f"  ID: {info.get('id')}",
        f"  会话ID: {info.get('session_id')}",
        f"  状态: {info.get('status')}",
        f"  路径: {workspace_dir}",
    ]
    
    if workspace_dir and os.path.exists(workspace_dir):
        total_size = 0
        file_count = 0
        dir_count = 0
        for root, dirs, files in os.walk(workspace_dir):
            dir_count += len(dirs)
            for f in files:
                file_count += 1
                total_size += os.path.getsize(os.path.join(root, f))
        result_lines.extend([
            f"  文件数: {file_count}",
            f"  目录数: {dir_count}",
            f"  总大小: {_format_file_size(total_size)}",
        ])
    
    result = "\n".join(result_lines)
    console.success("获取工作区信息成功")
    return {"result": result, "error": None}


def _execute_search_files(tool_args: dict, workspace_id: str, workspace_service) -> dict:
    pattern = tool_args.get("pattern", "*")
    console.info(f"搜索文件: pattern={pattern}, workspace={workspace_id}")
    
    workspace_dir = workspace_service.get_workspace_dir(workspace_id)
    if not workspace_dir:
        console.error(f"工作区不存在: {workspace_id}")
        return {"result": None, "error": f"工作区不存在: {workspace_id}"}
    
    if not os.path.exists(workspace_dir):
        console.success("工作区目录不存在，无文件")
        return {"result": "工作区为空", "error": None}
    
    matches = []
    for root, dirs, files in os.walk(workspace_dir):
        for filename in files:
            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, workspace_dir)
                matches.append({
                    "name": filename,
                    "path": rel_path.replace("\\", "/"),
                    "size": os.path.getsize(full_path),
                })
        for dirname in dirs:
            if fnmatch.fnmatch(dirname.lower(), pattern.lower()):
                full_path = os.path.join(root, dirname)
                rel_path = os.path.relpath(full_path, workspace_dir)
                matches.append({
                    "name": dirname,
                    "path": rel_path.replace("\\", "/"),
                    "is_dir": True,
                })
    
    if not matches:
        console.success(f"未找到匹配 '{pattern}' 的文件")
        return {"result": f"未找到匹配 '{pattern}' 的文件", "error": None}
    
    result_lines = [f"找到 {len(matches)} 个匹配 '{pattern}' 的结果：\n"]
    for m in matches:
        icon = "📁" if m.get("is_dir") else "📄"
        size_str = "" if m.get("is_dir") else f" ({_format_file_size(m['size'])})"
        result_lines.append(f"  {icon} {m['path']}{size_str}")
    
    result = "\n".join(result_lines)
    console.success(f"找到 {len(matches)} 个匹配项")
    return {"result": result, "error": None}


def _execute_workspace_tool(tool_name: str, tool_args: dict, workspace_id: str, workspace_service) -> dict:
    console.section(f"Workspace 工具: {tool_name}")
    
    if workspace_service is None:
        from ...service import WorkspaceService
        workspace_service = WorkspaceService()
    
    if tool_name == "list_workspace_files":
        return _execute_list_workspace_files(workspace_id, workspace_service)
    elif tool_name == "get_workspace_info":
        return _execute_get_workspace_info(workspace_id, workspace_service)
    elif tool_name == "search_files":
        return _execute_search_files(tool_args, workspace_id, workspace_service)
    else:
        return {"result": None, "error": f"未知的 workspace 工具: {tool_name}"}


def _execute_thinking_tool_direct(
    task_description: str,
    llm_service,
    message_context: dict,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
) -> dict:
    if not llm_service:
        result = f"思考任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    console.info("调用 LLM 进行思考...")
    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", SegmentType.THINKING_START, {
            "task_description": task_description,
            "is_start": True
        })

    try:
        full_prompt = build_context_prompt(
            parent_chain_messages,
            current_conversation_messages,
            f"请思考并执行当前任务: {task_description}"
        )
        messages = [{"role": "user", "content": full_prompt}]

        def thinking_token_callback(token: str):
            if send_message:
                send_message(token, SegmentType.THINKING_DELTA, {
                    "task_description": task_description,
                    "is_delta": True
                })

        result = ""
        for chunk in llm_service.chat_stream(messages, THINK_SYSTEM_PROMPT, thinking_token_callback):
            result += chunk

        console.success("思考完成")

        if send_message:
            send_message("", SegmentType.THINKING_END, {
                "task_description": task_description,
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", SegmentType.THINKING_END, {
                "task_description": task_description,
                "is_end": True,
                "error": str(e)
            })
        return {"result": f"思考失败: {e}", "error": str(e)}


def _execute_chat_tool_direct(
    task_description: str,
    llm_service,
    message_context: dict,
    parent_chain_messages: List[dict],
    current_conversation_messages: List[dict],
) -> dict:
    if not llm_service:
        result = f"回复任务: {task_description} (LLM 服务未配置)"
        console.info(f"结果: {result}")
        return {"result": result, "error": None}

    console.info("调用 LLM 进行对话回复...")
    send_message = message_context.get("send_message") if message_context else None

    if send_message:
        send_message("", SegmentType.CHAT_START, {
            "task_description": task_description,
            "is_start": True
        })

    try:
        full_prompt = build_context_prompt(
            parent_chain_messages,
            current_conversation_messages,
            f"请向用户输出回复: {task_description}"
        )
        messages = [{"role": "user", "content": full_prompt}]

        def chat_token_callback(token: str):
            if send_message:
                send_message(token, SegmentType.CHAT_DELTA, {
                    "task_description": task_description,
                    "is_delta": True
                })

        result = ""
        for chunk in llm_service.chat_stream(messages, CHAT_SYSTEM_PROMPT, chat_token_callback):
            result += chunk

        console.success("对话回复完成")

        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": task_description,
                "is_end": True,
                "result": result
            })

        return {"result": result, "error": None}

    except Exception as e:
        console.error(f"LLM 调用失败: {e}")
        if send_message:
            send_message("", SegmentType.CHAT_END, {
                "task_description": task_description,
                "is_end": True,
                "error": str(e)
            })
        return {"result": f"对话回复失败: {e}", "error": str(e)}


def create_execute_node(llm_service=None, token_callback=None, settings_service=None, message_context=None):
    def execute_node(state: AgentState) -> dict:
        if message_context:
            cancel_check = message_context.get("cancel_check")
            if cancel_check:
                cancel_check()
        
        pending_tools = state.get("pending_tools", [])
        current_agent_type = state.get("agent_type") or "director_agent"
        parent_chain_messages = state.get("parent_chain_messages", [])
        current_conversation_messages = state.get("current_conversation_messages", [])
        workspace_id = state["workspace_id"]
        
        if pending_tools:
            tool_name = pending_tools[0].get("tool")
            tool_args = pending_tools[0].get("args", {})
            task_description = tool_args.get("description", "")
            
            console.step("执行节点", "分析节点", f"执行工具: {tool_name}")
            
            console.box("执行工具", {
                "工具名称": tool_name,
                "工具参数": tool_args
            })
            
            conversation_id = message_context.get("conversation_id") if message_context else None
            
            _write_tool_event(
                conversation_id,
                tool_name,
                "started",
                task_description=task_description,
            )
            
            if message_context:
                send_message = message_context.get("send_message")
                if send_message and tool_name not in SPECIAL_TOOLS:
                    send_message("", SegmentType.TOOL_CALL, {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "task_description": task_description
                    })
            
            tool_args_copy = tool_args.copy()
            
            if tool_name in FILE_TOOLS:
                path_key = "path" if "path" in tool_args_copy else "file_path"
                target_path = tool_args_copy.get(path_key) or tool_args_copy.get("directory")
                if target_path:
                    allowed, resolved_path = workspace_service.resolve_path(workspace_id, target_path)
                    if allowed:
                        if "path" in tool_args_copy:
                            tool_args_copy["path"] = resolved_path
                        elif "file_path" in tool_args_copy:
                            tool_args_copy["file_path"] = resolved_path
                        elif "directory" in tool_args_copy:
                            tool_args_copy["directory"] = resolved_path
            
            if tool_name in EXPLORE_TOOLS:
                workspace_root = workspace_service.get_workspace_dir(workspace_id)
                if workspace_root:
                    tool_args_copy["workspace_root"] = workspace_root
            
            if tool_name == "thinking":
                tool_result = _execute_thinking_tool_direct(
                    task_description=task_description,
                    llm_service=llm_service,
                    message_context=message_context,
                    parent_chain_messages=parent_chain_messages,
                    current_conversation_messages=current_conversation_messages,
                )
            elif tool_name == "chat":
                tool_result = _execute_chat_tool_direct(
                    task_description=task_description,
                    llm_service=llm_service,
                    message_context=message_context,
                    parent_chain_messages=parent_chain_messages,
                    current_conversation_messages=current_conversation_messages,
                )
            elif tool_name == "read_file":
                tool_result = _execute_read_file(tool_args_copy)
            elif tool_name == "write_file":
                tool_result = _execute_write_file(tool_args_copy)
            elif tool_name == "delete_file":
                tool_result = _execute_delete_file(tool_args_copy)
            elif tool_name == "list_dir":
                tool_result = _execute_list_dir(tool_args_copy)
            elif tool_name == "create_dir":
                tool_result = _execute_create_dir(tool_args_copy)
            elif tool_name == "explore_code":
                tool_result = _execute_explore_code(tool_args_copy)
            elif tool_name == "explore_internet":
                tool_result = _execute_explore_internet(tool_args_copy)
            elif tool_name == "call_explore_agent":
                tool_result = _execute_call_explore_agent(
                    tool_args_copy, llm_service, token_callback, message_context,
                    parent_chain_messages, current_conversation_messages
                )
            elif tool_name == "call_review_agent":
                tool_result = _execute_call_review_agent(
                    tool_args_copy, llm_service, token_callback, message_context,
                    parent_chain_messages, current_conversation_messages
                )
            elif tool_name in WORKSPACE_TOOLS:
                tool_result = _execute_workspace_tool(tool_name, tool_args_copy, workspace_id, workspace_service)
            else:
                tool_result = {"result": f"工具 {tool_name} 执行成功", "error": None}
            
            result_str = str(tool_result.get("result", ""))
            console.box("工具执行结果", result_str[:200])
            
            if message_context:
                send_message = message_context.get("send_message")
                if send_message:
                    result_content = tool_result.get("result")
                    if result_content is None:
                        result_preview = ""
                    else:
                        result_preview = str(result_content)
                        if len(result_preview) > 500:
                            result_preview = result_preview[:500] + "..."
                    send_message("", SegmentType.TOOL_RES, {
                        "tool_name": tool_name,
                        "result": result_preview,
                        "error": tool_result.get("error"),
                        "success": tool_result.get("error") is None
                    })
            
            if tool_result.get("error") is None:
                _write_tool_event(
                    conversation_id,
                    tool_name,
                    "completed",
                    result=tool_result.get("result") or "",
                )
            else:
                _write_tool_event(
                    conversation_id,
                    tool_name,
                    "failed",
                    error=str(tool_result.get("error")),
                )
            
            new_tool_history = state.get("tool_history", []) + [{
                "tool": tool_name,
                "args": tool_args,
                "result": tool_result.get("result")
            }]
            
            new_current_conv_msgs = list(current_conversation_messages)
            new_current_conv_msgs.append({
                "role": "assistant",
                "content": f"[工具执行: {tool_name}]\n结果: {result_str[:1000]}"
            })
            
            has_more_tools = len(pending_tools) > 1
            is_chat_tool = tool_name == "chat"

            if is_chat_tool:
                console.decision_box("done", "工具输出最终回复，结束循环")
                return {
                    "pending_tools": pending_tools[1:],
                    "tool_history": new_tool_history,
                    "current_conversation_messages": new_current_conv_msgs,
                    "has_tool_use": False,
                    "final_reply": result_str
                }
            
            console.decision_box("execute" if has_more_tools else "analyze", "继续执行或分析")
            
            return {
                "pending_tools": pending_tools[1:],
                "tool_history": new_tool_history,
                "current_conversation_messages": new_current_conv_msgs,
                "has_tool_use": has_more_tools
            }
        
        if state.get("plan") and state.get("current_step", 0) < len(state["plan"]):
            step = state.get("current_step", 0)
            plan = state["plan"]
            task = plan[step]
            
            phase = task.get('phase', 'implementation')
            phase_names = {
                'research': '研究阶段',
                'synthesis': '综合阶段',
                'implementation': '实现阶段',
                'verification': '验证阶段'
            }
            phase_name = phase_names.get(phase, phase)
            
            console.step("执行节点", "规划节点", task['description'])
            
            tool_name = task.get("tool") or "thinking"
            tool_args = task.get("args") or {}
            
            console.execution_box(
                step + 1, len(plan), phase_name,
                task['description'], tool_name, tool_args
            )
            
            if tool_name == "thinking":
                tool_result = _execute_thinking_tool_direct(
                    task_description=task.get("description", ""),
                    llm_service=llm_service,
                    message_context=message_context,
                    parent_chain_messages=parent_chain_messages,
                    current_conversation_messages=current_conversation_messages,
                )
            elif tool_name == "chat":
                tool_result = _execute_chat_tool_direct(
                    task_description=task.get("description", ""),
                    llm_service=llm_service,
                    message_context=message_context,
                    parent_chain_messages=parent_chain_messages,
                    current_conversation_messages=current_conversation_messages,
                )
            else:
                tool_result = {"result": f"工具 {tool_name} 执行成功", "error": None}
            
            result_str = str(tool_result.get("result", ""))
            task["status"] = "completed" if tool_result.get("result") else "failed"
            task["result"] = result_str
            
            if phase == "research":
                task["feedback"] = f"研究完成：{result_str[:100]}..."
            elif phase == "synthesis":
                task["feedback"] = f"综合完成：制定了实现规范"
            elif phase == "implementation":
                task["feedback"] = f"实现完成：{result_str[:100]}..."
            elif phase == "verification":
                task["feedback"] = f"验证完成：{result_str[:100]}..."
            
            console.result_box(task['status'], result_str[:200], task['feedback'])
            
            new_results = state.get("results", []) + [{
                "task": task,
                "result": tool_result
            }]
            
            new_tool_history = state.get("tool_history", []) + [{
                "tool": tool_name,
                "args": tool_args,
                "result": tool_result.get("result")
            }]
            
            new_current_conv_msgs = list(current_conversation_messages)
            new_current_conv_msgs.append({
                "role": "assistant",
                "content": f"[工具执行: {tool_name}]\n结果: {result_str[:1000]}"
            })
            
            has_more_steps = step + 1 < len(plan)
            is_chat_tool = tool_name == "chat"
            
            if is_chat_tool:
                console.decision_box("done", "chat 工具输出最终回复，结束循环")
                return {
                    "current_step": step + 1,
                    "results": new_results,
                    "tool_history": new_tool_history,
                    "current_conversation_messages": new_current_conv_msgs,
                    "plan": plan,
                    "has_tool_use": False,
                    "final_reply": result_str
                }
            
            console.decision_box("execute" if has_more_steps else "done", "继续执行或完成")
            
            return {
                "current_step": step + 1,
                "results": new_results,
                "tool_history": new_tool_history,
                "current_conversation_messages": new_current_conv_msgs,
                "plan": plan,
                "has_tool_use": has_more_steps
            }
        
        console.step("执行节点", "无", "没有任务可执行")
        console.decision_box("done", "没有任务可执行，执行完成")
        
        return {
            "pending_tools": [],
            "in_plan_mode": False,
            "active_subagent": False,
            "execution_mode": None,
            "has_tool_use": False
        }
    
    return execute_node


def create_subagent_node(llm_service=None, token_callback=None, settings_service=None, message_context=None):
    def subagent_node(state: AgentState) -> dict:
        suggested_agent = state.get("suggested_subagent")
        user_message = state["messages"][-1] if state["messages"] else ""
        parent_chain_messages = state.get("parent_chain_messages", [])
        current_conversation_messages = state.get("current_conversation_messages", [])

        console.step("子代理节点", "分析节点", f"启动 {suggested_agent}")

        if suggested_agent in {"explore", "explore_agent"}:
            result = _execute_call_explore_agent(
                {"task_description": user_message},
                llm_service,
                token_callback,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
            )
        elif suggested_agent in {"review", "review_agent"}:
            result = _execute_call_review_agent(
                {"task_description": user_message},
                llm_service,
                token_callback,
                message_context,
                parent_chain_messages,
                current_conversation_messages,
            )
        else:
            result = {"result": None, "error": f"未知子代理: {suggested_agent}"}

        result_text = result.get("result") or result.get("error") or "子代理未返回结果"
        console.box("子代理结果", result_text[:200])

        return {
            "explore_result": result.get("result"),
            "active_subagent": False,
            "pending_tools": [
                {"tool": "chat", "args": {"description": f"总结子代理结果并回复用户: {result_text[:200]}"}}
            ],
            "has_tool_use": True
        }

    return subagent_node


def create_orchestrator_graph_v3(llm_service=None, token_callback=None, memory_mode: str = "accumulate", window_size: int = 3, settings_service=None, message_context=None):
    graph = StateGraph(AgentState)
    
    graph.add_node("analyze", create_analyze_node(llm_service, message_context, settings_service))
    graph.add_node("plan", create_plan_node(llm_service, token_callback, settings_service, message_context))
    graph.add_node("execute", create_execute_node(llm_service, token_callback, settings_service, message_context))
    graph.add_node("subagent", create_subagent_node(llm_service, token_callback, settings_service, message_context))
    
    graph.set_entry_point("analyze")
    
    graph.add_conditional_edges("analyze", route_after_analyze, {
        "plan": "plan",
        "execute": "execute",
        "subagent": "subagent",
        "done": END
    })
    
    graph.add_edge("plan", END)
    graph.add_edge("subagent", "execute")
    graph.add_conditional_edges("execute", check_state_v3, {
        "analyze": "analyze",
        "execute": "execute",
        "plan": "plan",
        "subagent": "subagent",
        "done": END
    })
    
    return graph.compile()


def run_graph_v3(
    user_message: str,
    workspace_id: str,
    llm_service=None,
    token_callback=None,
    memory_mode: str = "accumulate",
    window_size: int = 3,
    settings_service=None,
    message_context: dict = None,
    parent_chain_messages: List[dict] = None,
    current_conversation_messages: List[dict] = None
) -> dict:
    print("\n" + "="*60)
    print("[Director Agent] 块类型驱动循环 + Plan/Execute 分离")
    print(f"[Director Agent] 记忆模式: {memory_mode}, 窗口大小: {window_size}")
    print("="*60)
    
    initial_state = build_initial_state(
        user_message=user_message,
        workspace_id=workspace_id,
        parent_chain_messages=parent_chain_messages,
        current_conversation_messages=current_conversation_messages,
        is_root_graph=True,
    )
    
    graph = create_orchestrator_graph_v3(llm_service, token_callback, memory_mode, window_size, settings_service, message_context)
    final_state = graph.invoke(initial_state)

    if final_state.get("is_root_graph") and message_context:
        send_message = message_context.get("send_message")
        if send_message:
            send_message("", SegmentType.DONE, {
                "message_id": message_context.get("message_id", "")
            })
    
    print("\n" + "="*60)
    print("[Director Agent] 主编排图执行完成")
    print("="*60)
    
    return final_state


run_graph_v2 = run_graph_v3
create_orchestrator_graph_v2 = create_orchestrator_graph_v3
check_state_v2 = check_state_v3
