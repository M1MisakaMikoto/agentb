"""
Mock Factory for Robustness Testing

Provides mock objects for LLM service, tools, and other components
to simulate various failure scenarios.
"""

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
from unittest.mock import AsyncMock, MagicMock


class FailureType(Enum):
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    INVALID_RESPONSE = "invalid_response"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    MALFORMED_JSON = "malformed_json"
    EMPTY_RESPONSE = "empty_response"
    PARTIAL_RESPONSE = "partial_response"


@dataclass
class MockLLMConfig:
    failure_type: Optional[FailureType] = None
    delay_seconds: float = 0.0
    failure_rate: float = 0.0
    max_retries: int = 3
    response_template: Optional[str] = None
    call_count: int = 0
    failure_after_calls: int = -1


class MockLLMService:
    """Mock LLM Service that can simulate various failure scenarios."""
    
    def __init__(self, config: MockLLMConfig = None):
        self.config = config or MockLLMConfig()
        self.call_history: List[Dict[str, Any]] = []
        self._current_call = 0
    
    def _should_fail(self) -> bool:
        if self.config.failure_after_calls > 0:
            return self._current_call >= self.config.failure_after_calls
        if self.config.failure_rate > 0:
            import random
            return random.random() < self.config.failure_rate
        return self.config.failure_type is not None
    
    def _generate_response(self, messages: List[Dict], **kwargs) -> str:
        if self.config.response_template:
            return self.config.response_template
        
        return json.dumps({
            "tasks": [
                {"id": 1, "description": "分析用户需求", "tool": "thinking", "args": {}},
                {"id": 2, "description": "执行任务", "tool": "execute", "args": {}},
            ]
        })
    
    def chat(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None, **kwargs) -> str:
        self._current_call += 1
        self.call_history.append({
            "messages": messages,
            "system_prompt": system_prompt,
            "kwargs": kwargs,
            "timestamp": time.time(),
        })
        
        if self.config.delay_seconds > 0:
            time.sleep(self.config.delay_seconds)
        
        if self._should_fail():
            self._raise_failure()
        
        return self._generate_response(messages)
    
    def chat_stream(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None, 
                    stream_callback: Optional[Callable[[str], None]] = None, **kwargs):
        self._current_call += 1
        self.call_history.append({
            "messages": messages,
            "system_prompt": system_prompt,
            "stream": True,
            "kwargs": kwargs,
            "timestamp": time.time(),
        })
        
        if self.config.delay_seconds > 0:
            time.sleep(self.config.delay_seconds)
        
        if self._should_fail():
            self._raise_failure()
        
        response = self._generate_response(messages)
        for char in response:
            if stream_callback:
                stream_callback(char)
            yield char
    
    def structured_output(self, messages: List[Dict[str, str]], schema: Any, 
                          system_prompt: Optional[str] = None, **kwargs) -> Any:
        self._current_call += 1
        self.call_history.append({
            "messages": messages,
            "schema": schema,
            "system_prompt": system_prompt,
            "kwargs": kwargs,
            "timestamp": time.time(),
        })
        
        if self.config.delay_seconds > 0:
            time.sleep(self.config.delay_seconds)
        
        if self._should_fail():
            self._raise_failure()
        
        return {"tasks": [{"id": 1, "description": "测试任务", "tool": "thinking"}]}
    
    def _raise_failure(self):
        failure = self.config.failure_type
        
        if failure == FailureType.TIMEOUT:
            raise TimeoutError("LLM request timed out after 120 seconds")
        elif failure == FailureType.CONNECTION_ERROR:
            raise ConnectionError("Failed to connect to LLM service")
        elif failure == FailureType.INVALID_RESPONSE:
            raise ValueError("Invalid response from LLM: expected JSON")
        elif failure == FailureType.RATE_LIMIT:
            raise Exception("Rate limit exceeded: 429 Too Many Requests")
        elif failure == FailureType.SERVER_ERROR:
            raise Exception("Server error: 500 Internal Server Error")
        elif failure == FailureType.MALFORMED_JSON:
            raise ValueError("Malformed JSON response: {invalid json")
        elif failure == FailureType.EMPTY_RESPONSE:
            return ""
        elif failure == FailureType.PARTIAL_RESPONSE:
            return '{"tasks": [{"id": 1, "description": "incomplete'


class MockToolExecutor:
    """Mock Tool Executor for testing tool execution scenarios."""
    
    def __init__(self):
        self.execution_history: List[Dict[str, Any]] = []
        self._failure_config: Dict[str, FailureType] = {}
        self._delay_config: Dict[str, float] = {}
    
    def set_tool_failure(self, tool_name: str, failure_type: FailureType):
        self._failure_config[tool_name] = failure_type
    
    def set_tool_delay(self, tool_name: str, delay_seconds: float):
        self._delay_config[tool_name] = delay_seconds
    
    async def execute(self, tool_name: str, args: dict, context: dict) -> dict:
        self.execution_history.append({
            "tool": tool_name,
            "args": args,
            "context": context,
            "timestamp": time.time(),
        })
        
        if tool_name in self._delay_config:
            await asyncio.sleep(self._delay_config[tool_name])
        
        if tool_name in self._failure_config:
            return self._generate_failure_result(self._failure_config[tool_name])
        
        return self._generate_success_result(tool_name, args)
    
    def _generate_success_result(self, tool_name: str, args: dict) -> dict:
        return {
            "status": "success",
            "tool": tool_name,
            "result": f"Executed {tool_name} with args: {args}",
        }
    
    def _generate_failure_result(self, failure_type: FailureType) -> dict:
        if failure_type == FailureType.TIMEOUT:
            return {"error": "Tool execution timed out", "error_type": "timeout"}
        elif failure_type == FailureType.INVALID_RESPONSE:
            return {"error": "Invalid tool response", "error_type": "invalid_response"}
        else:
            return {"error": f"Tool failed: {failure_type.value}", "error_type": "execution_error"}


class MockToolRegistry:
    """Mock Tool Registry for testing tool availability scenarios."""
    
    def __init__(self, available_tools: List[str] = None):
        self._tools: Dict[str, MagicMock] = {}
        if available_tools:
            for tool_name in available_tools:
                self._register_mock_tool(tool_name)
    
    def _register_mock_tool(self, name: str):
        mock_tool = MagicMock()
        mock_tool.name = name
        mock_tool.executor = AsyncMock(return_value={"status": "success", "result": f"{name} executed"})
        self._tools[name] = mock_tool
    
    def get(self, name: str) -> Optional[MagicMock]:
        return self._tools.get(name)
    
    def list_tools(self) -> List[str]:
        return list(self._tools.keys())
    
    def register_tool(self, name: str, tool: MagicMock):
        self._tools[name] = tool
    
    def unregister_tool(self, name: str):
        self._tools.pop(name, None)


class MockMessageQueue:
    """Mock Message Queue for testing SSE streaming scenarios."""
    
    def __init__(self, block_after_messages: int = -1, block_duration: float = 0):
        self.messages: List[Dict[str, Any]] = []
        self._block_after = block_after_messages
        self._block_duration = block_duration
        self._message_count = 0
    
    async def put(self, conversation_id: str, message: Dict[str, Any]):
        self._message_count += 1
        
        if self._block_after > 0 and self._message_count >= self._block_after:
            await asyncio.sleep(self._block_duration)
        
        self.messages.append({"conversation_id": conversation_id, "message": message})
    
    async def get(self, conversation_id: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        for i, msg in enumerate(self.messages):
            if msg["conversation_id"] == conversation_id:
                return self.messages.pop(i)["message"]
        return None
    
    def clear(self):
        self.messages.clear()
        self._message_count = 0


class MockConversationDAO:
    """Mock Conversation DAO for testing database scenarios."""
    
    def __init__(self):
        self._sessions: Dict[int, Dict] = {}
        self._conversations: Dict[str, Dict] = {}
        self._next_session_id = 1
        self._should_fail = False
        self._failure_type: Optional[FailureType] = None
    
    def set_failure(self, failure_type: FailureType):
        self._should_fail = True
        self._failure_type = failure_type
    
    def clear_failure(self):
        self._should_fail = False
        self._failure_type = None
    
    def _check_failure(self):
        if self._should_fail:
            if self._failure_type == FailureType.CONNECTION_ERROR:
                raise ConnectionError("Database connection lost")
            elif self._failure_type == FailureType.TIMEOUT:
                raise TimeoutError("Database query timed out")
    
    async def create_session(self, user_id: int, title: str, workspace_id: str) -> Dict:
        self._check_failure()
        session_id = self._next_session_id
        self._next_session_id += 1
        session = {
            "id": session_id,
            "user_id": user_id,
            "title": title,
            "workspace_id": workspace_id,
        }
        self._sessions[session_id] = session
        return session
    
    async def get_session_by_id(self, session_id: int) -> Optional[Dict]:
        self._check_failure()
        return self._sessions.get(session_id)
    
    async def create_conversation(self, conversation_id: str, session_id: int, user_content: str):
        self._check_failure()
        self._conversations[conversation_id] = {
            "id": conversation_id,
            "session_id": session_id,
            "user_content": user_content,
        }
    
    async def delete_session(self, session_id: int):
        self._check_failure()
        self._sessions.pop(session_id, None)


class MockSettingsService:
    """Mock Settings Service for testing configuration scenarios."""
    
    def __init__(self, settings: Dict[str, Any] = None):
        self._settings = settings or {
            "llm:api_key": "test-api-key",
            "llm:base_url": "https://api.test.com/v1",
            "llm:model": "test-model",
            "llm:temperature": 0.7,
            "llm:max_tokens": 4096,
        }
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)
    
    def set(self, key: str, value: Any):
        self._settings[key] = value


def create_mock_agent_state(
    workspace_id: str = "test-workspace",
    plan: List[Dict] = None,
    current_step: int = 0,
    tool_history: List[Dict] = None,
    replan_count: int = 0,
    plan_failed: bool = False,
) -> Dict[str, Any]:
    """Factory function to create mock AgentState."""
    return {
        "messages": [],
        "workspace_id": workspace_id,
        "plan": plan or [{"id": 1, "description": "Test task", "tool": "thinking", "args": {}}],
        "current_step": current_step,
        "results": [],
        "plan_failed": plan_failed,
        "explore_result": None,
        "tool_history": tool_history or [],
        "replan_count": replan_count,
        "agent_type": "build_agent",
        "intent_analysis": None,
        "parent_chain_messages": None,
        "current_conversation_messages": None,
        "execution_mode": None,
        "mode_reason": None,
        "suggested_tools": None,
        "in_plan_mode": False,
        "pending_tools": None,
    }


import json
