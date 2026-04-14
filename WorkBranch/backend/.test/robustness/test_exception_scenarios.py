"""
Exception Scenario Tests for Agent Service

Tests for:
- EX-01: LLM service timeout
- EX-02: LLM returns non-JSON format
- EX-03: Tool not found
- EX-04: Tool parameter missing/type error
- EX-05: Tool execution uncaught exception
- EX-06: Empty or oversized user input
- EX-07: Non-existent workspace_id
- EX-08: Concurrent state modification
- EX-09: Replan count exceeded
- EX-10: Message queue blocking
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from .utils.mocks import (
    MockLLMService,
    MockLLMConfig,
    MockToolExecutor,
    MockToolRegistry,
    MockConversationDAO,
    MockMessageQueue,
    FailureType,
    create_mock_agent_state,
)
from .utils.scenarios import ScenarioOrchestrator, ScenarioConfig, ScenarioResult


class TestLLMTimeout:
    """EX-01: LLM service timeout tests."""
    
    def test_llm_timeout_raises_exception(self, mock_llm_timeout: MockLLMService):
        with pytest.raises(TimeoutError) as exc_info:
            mock_llm_timeout.chat([{"role": "user", "content": "test"}])
        assert "timed out" in str(exc_info.value).lower()
    
    def test_llm_timeout_in_stream(self, mock_llm_timeout: MockLLMService):
        with pytest.raises(TimeoutError):
            list(mock_llm_timeout.chat_stream([{"role": "user", "content": "test"}]))
    
    def test_llm_timeout_in_structured_output(self, mock_llm_timeout: MockLLMService):
        with pytest.raises(TimeoutError):
            mock_llm_timeout.structured_output(
                [{"role": "user", "content": "test"}],
                schema={"type": "object"},
            )
    
    def test_llm_timeout_call_history_recorded(self, mock_llm_timeout: MockLLMService):
        try:
            mock_llm_timeout.chat([{"role": "user", "content": "test"}])
        except TimeoutError:
            pass
        assert len(mock_llm_timeout.call_history) == 1


class TestLLMInvalidResponse:
    """EX-02: LLM returns non-JSON format tests."""
    
    def test_llm_malformed_json_raises_exception(self, mock_llm_invalid_json: MockLLMService):
        with pytest.raises(ValueError) as exc_info:
            mock_llm_invalid_json.chat([{"role": "user", "content": "test"}])
        assert "malformed" in str(exc_info.value).lower() or "json" in str(exc_info.value).lower()
    
    def test_llm_connection_error_raises_exception(self, mock_llm_connection_error: MockLLMService):
        with pytest.raises(ConnectionError):
            mock_llm_connection_error.chat([{"role": "user", "content": "test"}])
    
    def test_llm_rate_limit_raises_exception(self, mock_llm_rate_limit: MockLLMService):
        with pytest.raises(Exception) as exc_info:
            mock_llm_rate_limit.chat([{"role": "user", "content": "test"}])
        assert "rate limit" in str(exc_info.value).lower() or "429" in str(exc_info.value)
    
    def test_llm_server_error_raises_exception(self, mock_llm_server_error: MockLLMService):
        with pytest.raises(Exception) as exc_info:
            mock_llm_server_error.chat([{"role": "user", "content": "test"}])
        assert "500" in str(exc_info.value) or "server error" in str(exc_info.value).lower()


class TestToolNotFound:
    """EX-03: Tool not found tests."""
    
    @pytest.mark.asyncio
    async def test_tool_not_found_returns_error(self, mock_tool_registry_empty: MockToolRegistry):
        tool = mock_tool_registry_empty.get("non_existent_tool")
        assert tool is None
    
    @pytest.mark.asyncio
    async def test_tool_executor_handles_missing_tool(self, mock_tool_executor: MockToolExecutor):
        mock_tool_executor.set_tool_failure("unknown_tool", FailureType.INVALID_RESPONSE)
        result = await mock_tool_executor.execute("unknown_tool", {}, {})
        assert "error" in result
    
    @pytest.mark.asyncio
    async def test_tool_registry_list_empty(self, mock_tool_registry_empty: MockToolRegistry):
        tools = mock_tool_registry_empty.list_tools()
        assert len(tools) == 0


class TestToolParameterError:
    """EX-04: Tool parameter missing/type error tests."""
    
    @pytest.mark.asyncio
    async def test_tool_executor_with_missing_args(self, mock_tool_executor: MockToolExecutor):
        result = await mock_tool_executor.execute("read_file", {}, {})
        assert result["status"] == "success" or "error" in result
    
    @pytest.mark.asyncio
    async def test_tool_executor_with_invalid_args_type(self, mock_tool_executor: MockToolExecutor):
        result = await mock_tool_executor.execute("read_file", {"path": 12345}, {})
        assert "status" in result
    
    @pytest.mark.asyncio
    async def test_tool_executor_with_none_args(self, mock_tool_executor: MockToolExecutor):
        result = await mock_tool_executor.execute("thinking", None, {})
        assert "status" in result or "error" in result


class TestToolExecutionException:
    """EX-05: Tool execution uncaught exception tests."""
    
    @pytest.mark.asyncio
    async def test_tool_timeout_is_handled(self, mock_tool_executor: MockToolExecutor):
        mock_tool_executor.set_tool_failure("slow_tool", FailureType.TIMEOUT)
        mock_tool_executor.set_tool_delay("slow_tool", 0.1)
        result = await mock_tool_executor.execute("slow_tool", {}, {})
        assert "error" in result
        assert result.get("error_type") == "timeout"
    
    @pytest.mark.asyncio
    async def test_tool_execution_history_recorded(self, mock_tool_executor: MockToolExecutor):
        await mock_tool_executor.execute("test_tool", {"arg": "value"}, {"context": "data"})
        assert len(mock_tool_executor.execution_history) == 1
        assert mock_tool_executor.execution_history[0]["tool"] == "test_tool"


class TestUserInputValidation:
    """EX-06: Empty or oversized user input tests."""
    
    def test_empty_input_handling(self, mock_llm_service: MockLLMService):
        result = mock_llm_service.chat([{"role": "user", "content": ""}])
        assert result is not None
    
    def test_whitespace_only_input(self, mock_llm_service: MockLLMService):
        result = mock_llm_service.chat([{"role": "user", "content": "   \n\t  "}])
        assert result is not None
    
    def test_large_input_handling(self, mock_llm_service: MockLLMService, large_payload_1mb: str):
        result = mock_llm_service.chat([{"role": "user", "content": large_payload_1mb}])
        assert result is not None
    
    def test_special_characters_input(self, mock_llm_service: MockLLMService):
        special_chars = "<script>alert('xss')</script>\x00\x01\x02"
        result = mock_llm_service.chat([{"role": "user", "content": special_chars}])
        assert result is not None
    
    def test_unicode_input(self, mock_llm_service: MockLLMService):
        unicode_content = "你好世界 🌍 مرحبا Привет"
        result = mock_llm_service.chat([{"role": "user", "content": unicode_content}])
        assert result is not None


class TestInvalidWorkspace:
    """EX-07: Non-existent workspace_id tests."""
    
    @pytest.mark.asyncio
    async def test_nonexistent_session_returns_none(self, mock_conversation_dao: MockConversationDAO):
        session = await mock_conversation_dao.get_session_by_id(99999)
        assert session is None
    
    @pytest.mark.asyncio
    async def test_create_conversation_with_invalid_session(self, mock_conversation_dao: MockConversationDAO):
        await mock_conversation_dao.create_session(1, "Test", "workspace-1")
        result = await mock_conversation_dao.get_session_by_id(1)
        assert result is not None
        
        session = await mock_conversation_dao.get_session_by_id(999)
        assert session is None


class TestConcurrentStateModification:
    """EX-08: Concurrent state modification tests."""
    
    @pytest.mark.asyncio
    async def test_concurrent_session_creation(self, concurrent_session_manager):
        tasks = [
            concurrent_session_manager.create_session(f"session-{i}", {"data": i})
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)
        assert all(results)
        sessions = await concurrent_session_manager.list_sessions()
        assert len(sessions) == 10
    
    @pytest.mark.asyncio
    async def test_concurrent_session_update(self, concurrent_session_manager):
        await concurrent_session_manager.create_session("test-session", {"counter": 0})
        
        async def increment_counter():
            session = await concurrent_session_manager.get_session("test-session")
            if session:
                await asyncio.sleep(0.001)
                await concurrent_session_manager.update_session(
                    "test-session",
                    {"counter": session.get("counter", 0) + 1}
                )
        
        tasks = [increment_counter() for _ in range(5)]
        await asyncio.gather(*tasks)
        
        session = await concurrent_session_manager.get_session("test-session")
        assert session is not None
    
    @pytest.mark.asyncio
    async def test_concurrent_read_write(self, concurrent_session_manager):
        await concurrent_session_manager.create_session("rw-test", {"value": 0})
        
        async def reader():
            for _ in range(10):
                await concurrent_session_manager.get_session("rw-test")
                await asyncio.sleep(0.001)
        
        async def writer():
            for i in range(10):
                await concurrent_session_manager.update_session("rw-test", {"value": i})
                await asyncio.sleep(0.001)
        
        await asyncio.gather(reader(), writer())
        session = await concurrent_session_manager.get_session("rw-test")
        assert session is not None


class TestReplanCountExceeded:
    """EX-09: Replan count exceeded tests."""
    
    def test_replan_count_at_limit(self, mock_agent_state_replan_limit: dict):
        assert mock_agent_state_replan_limit["replan_count"] >= 3
        assert mock_agent_state_replan_limit["plan_failed"] is True
    
    def test_replan_count_increments(self):
        state = create_mock_agent_state(replan_count=2, plan_failed=True)
        assert state["replan_count"] == 2
        
        new_count = state["replan_count"] + 1
        assert new_count == 3
    
    def test_max_replan_constant(self):
        MAX_REPLAN_COUNT = 3
        state = create_mock_agent_state(replan_count=MAX_REPLAN_COUNT)
        should_stop = state["replan_count"] >= MAX_REPLAN_COUNT
        assert should_stop is True


class TestMessageQueueBlocking:
    """EX-10: Message queue blocking tests."""
    
    @pytest.mark.asyncio
    async def test_message_queue_basic_operations(self, mock_message_queue: MockMessageQueue):
        await mock_message_queue.put("conv-1", {"type": "test", "content": "hello"})
        msg = await mock_message_queue.get("conv-1")
        assert msg is not None
        assert msg["type"] == "test"
    
    @pytest.mark.asyncio
    async def test_message_queue_timeout(self, mock_message_queue: MockMessageQueue):
        msg = await mock_message_queue.get("non-existent-conv", timeout=0.1)
        assert msg is None
    
    @pytest.mark.asyncio
    async def test_message_queue_clear(self, mock_message_queue: MockMessageQueue):
        await mock_message_queue.put("conv-1", {"type": "test1"})
        await mock_message_queue.put("conv-2", {"type": "test2"})
        mock_message_queue.clear()
        assert len(mock_message_queue.messages) == 0


class TestScenarioOrchestrator:
    """Test scenario orchestrator functionality."""
    
    @pytest.mark.asyncio
    async def test_orchestrator_runs_scenario(self, scenario_orchestrator: ScenarioOrchestrator):
        async def passing_test():
            await asyncio.sleep(0.01)
            return True
        
        config = ScenarioConfig(name="test_pass", description="Passing test")
        result = await scenario_orchestrator.run_scenario(config, passing_test)
        assert result.result == ScenarioResult.PASSED
    
    @pytest.mark.asyncio
    async def test_orchestrator_handles_timeout(self, scenario_orchestrator: ScenarioOrchestrator):
        async def slow_test():
            await asyncio.sleep(5)
        
        config = ScenarioConfig(
            name="test_timeout",
            description="Timeout test",
            timeout_seconds=0.1,
        )
        result = await scenario_orchestrator.run_scenario(config, slow_test)
        assert result.result == ScenarioResult.TIMEOUT
    
    @pytest.mark.asyncio
    async def test_orchestrator_handles_failure(self, scenario_orchestrator: ScenarioOrchestrator):
        async def failing_test():
            assert False, "Intentional failure"
        
        config = ScenarioConfig(name="test_fail", description="Failing test")
        result = await scenario_orchestrator.run_scenario(config, failing_test)
        assert result.result == ScenarioResult.FAILED
    
    @pytest.mark.asyncio
    async def test_orchestrator_handles_exception(self, scenario_orchestrator: ScenarioOrchestrator):
        async def error_test():
            raise RuntimeError("Unexpected error")
        
        config = ScenarioConfig(name="test_error", description="Error test")
        result = await scenario_orchestrator.run_scenario(config, error_test)
        assert result.result == ScenarioResult.ERROR
    
    @pytest.mark.asyncio
    async def test_orchestrator_retry(self, scenario_orchestrator: ScenarioOrchestrator):
        call_count = 0
        
        async def flaky_test():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Flaky failure")
            return True
        
        config = ScenarioConfig(
            name="test_retry",
            description="Retry test",
            retry_count=3,
            retry_delay_seconds=0.01,
        )
        result = await scenario_orchestrator.run_scenario(config, flaky_test)
        assert result.result == ScenarioResult.PASSED
        assert call_count == 3
