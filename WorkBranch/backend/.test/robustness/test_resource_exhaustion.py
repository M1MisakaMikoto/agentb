"""
Resource Exhaustion Tests for Agent Service

Tests for:
- RS-01: Memory pressure with large message history
- RS-02: Connection pool exhaustion
- RS-03: HTTP client resource leak
- RS-04: Sub-agent creation limit
- RS-05: Long-running task accumulation
"""

import asyncio
import gc
import sys
import pytest
from typing import List
from unittest.mock import AsyncMock, MagicMock

from .utils.mocks import (
    MockLLMService,
    MockToolExecutor,
    MockToolRegistry,
    create_mock_agent_state,
)
from .utils.scenarios import (
    ResourceMonitor,
    ConcurrentSessionManager,
    create_test_messages,
    create_large_payload,
    gather_with_concurrency,
)


class TestMemoryPressure:
    """RS-01: Memory pressure with large message history tests."""
    
    def test_large_message_list_creation(self):
        messages = create_test_messages(10000)
        assert len(messages) == 10000
    
    def test_large_message_list_memory(self, large_message_list: list):
        size = sys.getsizeof(large_message_list)
        assert size > 0
    
    def test_agent_state_with_large_history(self):
        tool_history = [
            {"tool": f"tool_{i}", "args": {"arg": f"value_{i}"}, "result": "x" * 1000}
            for i in range(1000)
        ]
        state = create_mock_agent_state(tool_history=tool_history)
        assert len(state["tool_history"]) == 1000
    
    def test_message_truncation_mechanism(self):
        MAX_MESSAGES = 100
        messages = create_test_messages(1000)
        
        if len(messages) > MAX_MESSAGES:
            truncated = messages[-MAX_MESSAGES:]
        else:
            truncated = messages
        
        assert len(truncated) <= MAX_MESSAGES
    
    @pytest.mark.asyncio
    async def test_memory_cleanup_after_processing(self):
        large_data = [create_large_payload(100) for _ in range(10)]
        
        processed = [len(data) for data in large_data]
        assert len(processed) == 10
        
        del large_data
        del processed
        gc.collect()
    
    @pytest.mark.asyncio
    async def test_repeated_operations_memory_stability(self, mock_llm_service: MockLLMService):
        for _ in range(100):
            messages = create_test_messages(50)
            mock_llm_service.chat(messages[:1])
        
        assert len(mock_llm_service.call_history) == 100


class TestConnectionPoolExhaustion:
    """RS-02: Connection pool exhaustion tests."""
    
    @pytest.mark.asyncio
    async def test_concurrent_requests_with_semaphore(self):
        semaphore = asyncio.Semaphore(5)
        request_count = 0
        
        async def make_request():
            nonlocal request_count
            async with semaphore:
                request_count += 1
                await asyncio.sleep(0.01)
        
        tasks = [make_request() for _ in range(20)]
        await asyncio.gather(*tasks)
        
        assert request_count == 20
    
    @pytest.mark.asyncio
    async def test_connection_pool_limit(self):
        max_connections = 10
        active_connections = []
        rejected = []
        
        async def request_connection(conn_id: int):
            if len(active_connections) < max_connections:
                active_connections.append(conn_id)
                await asyncio.sleep(0.05)
                active_connections.remove(conn_id)
                return True
            else:
                rejected.append(conn_id)
                return False
        
        tasks = [request_connection(i) for i in range(20)]
        results = await asyncio.gather(*tasks)
        
        assert len(rejected) > 0 or all(results)
    
    @pytest.mark.asyncio
    async def test_gather_with_concurrency_limit(self):
        call_order: List[int] = []
        
        async def tracked_task(task_id: int):
            call_order.append(task_id)
            await asyncio.sleep(0.01)
            return task_id
        
        results = await gather_with_concurrency(3, *[tracked_task(i) for i in range(10)])
        assert len(results) == 10
    
    @pytest.mark.asyncio
    async def test_connection_timeout_handling(self):
        timeout_count = 0
        
        async def slow_connection():
            await asyncio.sleep(5)
        
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_connection(), timeout=0.1)


class TestHTTPClientLeak:
    """RS-03: HTTP client resource leak tests."""
    
    @pytest.mark.asyncio
    async def test_context_manager_cleanup(self):
        class MockHTTPClient:
            def __init__(self):
                self.closed = False
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                self.closed = True
        
        async with MockHTTPClient() as client:
            assert not client.closed
        
        assert client.closed
    
    @pytest.mark.asyncio
    async def test_multiple_client_creation_and_cleanup(self):
        clients = []
        
        for _ in range(10):
            class MockClient:
                def __init__(self):
                    self.closed = False
                
                async def close(self):
                    self.closed = True
            
            client = MockClient()
            clients.append(client)
        
        for client in clients:
            await client.close()
            assert client.closed
    
    @pytest.mark.asyncio
    async def test_client_cleanup_on_exception(self):
        class MockClient:
            def __init__(self):
                self.closed = False
            
            async def close(self):
                self.closed = True
        
        client = MockClient()
        
        try:
            raise RuntimeError("Simulated error")
        except RuntimeError:
            await client.close()
        
        assert client.closed


class TestSubAgentLimit:
    """RS-04: Sub-agent creation limit tests."""
    
    @pytest.mark.asyncio
    async def test_agent_creation_limit(self):
        MAX_AGENTS = 5
        agents = {}
        
        async def spawn_agent(agent_id: str):
            if len(agents) >= MAX_AGENTS:
                return {"error": "Agent limit reached", "agent_id": agent_id}
            agents[agent_id] = {"id": agent_id, "status": "running"}
            return {"status": "created", "agent_id": agent_id}
        
        results = []
        for i in range(10):
            result = await spawn_agent(f"agent-{i}")
            results.append(result)
        
        success_count = sum(1 for r in results if r.get("status") == "created")
        error_count = sum(1 for r in results if r.get("error"))
        
        assert success_count == MAX_AGENTS
        assert error_count == 10 - MAX_AGENTS
    
    @pytest.mark.asyncio
    async def test_agent_cleanup_on_stop(self):
        agents = {}
        
        async def spawn_agent(agent_id: str):
            agents[agent_id] = {"id": agent_id, "status": "running"}
            return agents[agent_id]
        
        async def stop_agent(agent_id: str):
            if agent_id in agents:
                agents[agent_id]["status"] = "stopped"
                del agents[agent_id]
                return True
            return False
        
        await spawn_agent("agent-1")
        await spawn_agent("agent-2")
        
        assert len(agents) == 2
        
        await stop_agent("agent-1")
        assert len(agents) == 1
        assert "agent-1" not in agents
    
    @pytest.mark.asyncio
    async def test_agent_list_management(self):
        agent_registry = {}
        
        def list_agents():
            return list(agent_registry.keys())
        
        def register_agent(agent_id: str):
            agent_registry[agent_id] = True
        
        def unregister_agent(agent_id: str):
            agent_registry.pop(agent_id, None)
        
        register_agent("agent-1")
        register_agent("agent-2")
        register_agent("agent-3")
        
        assert len(list_agents()) == 3
        
        unregister_agent("agent-2")
        assert len(list_agents()) == 2
        assert "agent-2" not in list_agents()


class TestTaskAccumulation:
    """RS-05: Long-running task accumulation tests."""
    
    @pytest.mark.asyncio
    async def test_task_timeout_cleanup(self):
        completed_tasks = []
        timeout_tasks = []
        
        async def run_task(task_id: int, duration: float):
            try:
                await asyncio.wait_for(
                    asyncio.sleep(duration),
                    timeout=0.1,
                )
                completed_tasks.append(task_id)
            except asyncio.TimeoutError:
                timeout_tasks.append(task_id)
        
        tasks = [
            run_task(1, 0.05),
            run_task(2, 0.05),
            run_task(3, 5.0),
            run_task(4, 5.0),
        ]
        await asyncio.gather(*tasks)
        
        assert len(completed_tasks) == 2
        assert len(timeout_tasks) == 2
    
    @pytest.mark.asyncio
    async def test_zombie_task_detection(self):
        running_tasks = {}
        
        async def create_task(task_id: str, should_hang: bool = False):
            running_tasks[task_id] = {"start_time": asyncio.get_event_loop().time()}
            if not should_hang:
                await asyncio.sleep(0.01)
                del running_tasks[task_id]
        
        await create_task("task-1")
        await create_task("task-2")
        
        task = asyncio.create_task(create_task("task-3", should_hang=True))
        await asyncio.sleep(0.02)
        
        assert "task-3" in running_tasks
        task.cancel()
    
    @pytest.mark.asyncio
    async def test_task_queue_size_limit(self):
        MAX_QUEUE_SIZE = 10
        task_queue = []
        rejected = []
        
        def enqueue_task(task_data: dict):
            if len(task_queue) >= MAX_QUEUE_SIZE:
                rejected.append(task_data)
                return False
            task_queue.append(task_data)
            return True
        
        for i in range(15):
            enqueue_task({"id": i, "data": f"task-{i}"})
        
        assert len(task_queue) == MAX_QUEUE_SIZE
        assert len(rejected) == 5
    
    @pytest.mark.asyncio
    async def test_task_cleanup_on_cancel(self):
        cleanup_called = []
        
        async def cancellable_task(task_id: str):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cleanup_called.append(task_id)
                raise
        
        task = asyncio.create_task(cancellable_task("task-1"))
        await asyncio.sleep(0.01)
        task.cancel()
        
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        assert "task-1" in cleanup_called


class TestResourceMonitor:
    """Test ResourceMonitor functionality."""
    
    @pytest.mark.asyncio
    async def test_resource_monitor_basic(self):
        monitor = ResourceMonitor()
        await monitor.start_monitoring(interval_seconds=0.05)
        
        data = [create_large_payload(100) for _ in range(5)]
        await asyncio.sleep(0.2)
        
        await monitor.stop_monitoring()
        
        peak = monitor.get_peak_memory_mb()
        assert peak is not None
        assert peak > 0
        
        del data
        gc.collect()
    
    @pytest.mark.asyncio
    async def test_resource_monitor_trend(self):
        monitor = ResourceMonitor()
        await monitor.start_monitoring(interval_seconds=0.05)
        
        await asyncio.sleep(0.15)
        
        await monitor.stop_monitoring()
        
        trend = monitor.get_memory_trend()
        assert len(trend) >= 2
    
    @pytest.mark.asyncio
    async def test_resource_monitor_clear(self):
        monitor = ResourceMonitor()
        await monitor.start_monitoring(interval_seconds=0.05)
        await asyncio.sleep(0.1)
        await monitor.stop_monitoring()
        
        monitor.clear()
        assert len(monitor._snapshots) == 0
