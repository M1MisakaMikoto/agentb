"""
Pytest Fixtures for Robustness Testing

Provides fixtures for mocking LLM service, tools, database,
and other components needed for robustness tests.
"""

import asyncio
import pytest
from typing import AsyncGenerator, Generator

from .utils.mocks import (
    MockLLMConfig,
    MockLLMService,
    MockToolExecutor,
    MockToolRegistry,
    MockMessageQueue,
    MockConversationDAO,
    MockSettingsService,
    FailureType,
    create_mock_agent_state,
)
from .utils.scenarios import (
    ScenarioOrchestrator,
    ConcurrentSessionManager,
    ResourceMonitor,
)


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_llm_config() -> MockLLMConfig:
    return MockLLMConfig()


@pytest.fixture
def mock_llm_service(mock_llm_config: MockLLMConfig) -> MockLLMService:
    return MockLLMService(mock_llm_config)


@pytest.fixture
def mock_llm_timeout() -> MockLLMService:
    config = MockLLMConfig(failure_type=FailureType.TIMEOUT, delay_seconds=0.1)
    return MockLLMService(config)


@pytest.fixture
def mock_llm_connection_error() -> MockLLMService:
    config = MockLLMConfig(failure_type=FailureType.CONNECTION_ERROR)
    return MockLLMService(config)


@pytest.fixture
def mock_llm_invalid_json() -> MockLLMService:
    config = MockLLMConfig(failure_type=FailureType.MALFORMED_JSON)
    return MockLLMService(config)


@pytest.fixture
def mock_llm_rate_limit() -> MockLLMService:
    config = MockLLMConfig(failure_type=FailureType.RATE_LIMIT)
    return MockLLMService(config)


@pytest.fixture
def mock_llm_server_error() -> MockLLMService:
    config = MockLLMConfig(failure_type=FailureType.SERVER_ERROR)
    return MockLLMService(config)


@pytest.fixture
def mock_llm_intermittent() -> MockLLMService:
    config = MockLLMConfig(failure_rate=0.5)
    return MockLLMService(config)


@pytest.fixture
def mock_llm_delayed() -> MockLLMService:
    config = MockLLMConfig(delay_seconds=2.0)
    return MockLLMService(config)


@pytest.fixture
def mock_llm_fail_after_3_calls() -> MockLLMService:
    config = MockLLMConfig(failure_after_calls=3, failure_type=FailureType.SERVER_ERROR)
    return MockLLMService(config)


@pytest.fixture
def mock_tool_executor() -> MockToolExecutor:
    return MockToolExecutor()


@pytest.fixture
def mock_tool_registry() -> MockToolRegistry:
    return MockToolRegistry(available_tools=["thinking", "read_file", "write_file", "execute"])


@pytest.fixture
def mock_tool_registry_empty() -> MockToolRegistry:
    return MockToolRegistry(available_tools=[])


@pytest.fixture
def mock_message_queue() -> MockMessageQueue:
    return MockMessageQueue()


@pytest.fixture
def mock_message_queue_blocking() -> MockMessageQueue:
    return MockMessageQueue(block_after_messages=5, block_duration=10.0)


@pytest.fixture
def mock_conversation_dao() -> MockConversationDAO:
    return MockConversationDAO()


@pytest.fixture
def mock_conversation_dao_failing() -> MockConversationDAO:
    dao = MockConversationDAO()
    dao.set_failure(FailureType.CONNECTION_ERROR)
    return dao


@pytest.fixture
def mock_settings_service() -> MockSettingsService:
    return MockSettingsService()


@pytest.fixture
def mock_agent_state() -> dict:
    return create_mock_agent_state()


@pytest.fixture
def mock_agent_state_with_history() -> dict:
    tool_history = [
        {"tool": "read_file", "args": {"path": "/test/file.py"}, "result": "file content"},
        {"tool": "thinking", "args": {}, "result": "analysis complete"},
    ]
    return create_mock_agent_state(tool_history=tool_history)


@pytest.fixture
def mock_agent_state_replan_limit() -> dict:
    return create_mock_agent_state(replan_count=3, plan_failed=True)


@pytest.fixture
def scenario_orchestrator() -> ScenarioOrchestrator:
    return ScenarioOrchestrator()


@pytest.fixture
def concurrent_session_manager() -> ConcurrentSessionManager:
    return ConcurrentSessionManager(max_concurrent=10)


@pytest.fixture
def resource_monitor() -> ResourceMonitor:
    return ResourceMonitor()


@pytest.fixture
async def resource_monitor_started(resource_monitor: ResourceMonitor) -> AsyncGenerator[ResourceMonitor, None]:
    await resource_monitor.start_monitoring(interval_seconds=0.1)
    yield resource_monitor
    await resource_monitor.stop_monitoring()


@pytest.fixture
def large_message_list() -> list:
    from .utils.scenarios import create_test_messages
    return create_test_messages(1000)


@pytest.fixture
def large_payload_1mb() -> str:
    from .utils.scenarios import create_large_payload
    return create_large_payload(1024)


@pytest.fixture
def large_payload_10mb() -> str:
    from .utils.scenarios import create_large_payload
    return create_large_payload(10 * 1024)


@pytest.fixture(params=[
    "normal",
    "timeout",
    "connection_error",
    "invalid_json",
    "rate_limit",
    "server_error",
])
def mock_llm_service_various(request, mock_llm_service) -> MockLLMService:
    scenario = request.param
    if scenario == "normal":
        return mock_llm_service
    elif scenario == "timeout":
        return MockLLMService(MockLLMConfig(failure_type=FailureType.TIMEOUT))
    elif scenario == "connection_error":
        return MockLLMService(MockLLMConfig(failure_type=FailureType.CONNECTION_ERROR))
    elif scenario == "invalid_json":
        return MockLLMService(MockLLMConfig(failure_type=FailureType.MALFORMED_JSON))
    elif scenario == "rate_limit":
        return MockLLMService(MockLLMConfig(failure_type=FailureType.RATE_LIMIT))
    elif scenario == "server_error":
        return MockLLMService(MockLLMConfig(failure_type=FailureType.SERVER_ERROR))
    return mock_llm_service
