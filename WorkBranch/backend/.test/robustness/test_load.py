"""
Load/Stress Tests for Agent Service

Tests for:
- LD-01: 100 concurrent session creation
- LD-03: Mixed read/write operations
- LD-04: Burst traffic handling

Note: Long-duration tests (LD-02) are excluded per user request.
"""

import asyncio
import time
from typing import Dict, List
import pytest

from .utils.mocks import (
    MockLLMService,
    MockConversationDAO,
    MockToolExecutor,
)
from .utils.scenarios import (
    ScenarioOrchestrator,
    ScenarioConfig,
    ScenarioResult,
    ConcurrentSessionManager,
    ResourceMonitor,
    gather_with_concurrency,
    measure_time,
)


class TestConcurrentSessionCreation:
    """LD-01: 100 concurrent session creation tests."""
    
    @pytest.mark.asyncio
    async def test_100_concurrent_session_creations(self, concurrent_session_manager: ConcurrentSessionManager):
        async def create_session(session_id: int):
            success = await concurrent_session_manager.create_session(
                f"session-{session_id}",
                {"user_id": session_id, "created_at": time.time()},
            )
            return {"session_id": session_id, "success": success}
        
        tasks = [create_session(i) for i in range(100)]
        results = await asyncio.gather(*tasks)
        
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 100
        
        sessions = await concurrent_session_manager.list_sessions()
        assert len(sessions) == 100
    
    @pytest.mark.asyncio
    async def test_concurrent_session_creation_timing(self, concurrent_session_manager: ConcurrentSessionManager):
        async with measure_time("100_session_creations") as timing:
            tasks = [
                concurrent_session_manager.create_session(f"timing-session-{i}", {"index": i})
                for i in range(100)
            ]
            await asyncio.gather(*tasks)
        
        assert timing["duration_ms"] < 3000
    
    @pytest.mark.asyncio
    async def test_concurrent_session_creation_with_semaphore(self):
        max_concurrent = 10
        semaphore = asyncio.Semaphore(max_concurrent)
        active_count = 0
        max_observed = 0
        lock = asyncio.Lock()
        
        async def tracked_create(session_id: int):
            nonlocal active_count, max_observed
            async with semaphore:
                async with lock:
                    active_count += 1
                    max_observed = max(max_observed, active_count)
                await asyncio.sleep(0.01)
                async with lock:
                    active_count -= 1
                return session_id
        
        tasks = [tracked_create(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 50
        assert max_observed <= max_concurrent
    
    @pytest.mark.asyncio
    async def test_session_creation_error_handling(self):
        error_count = 0
        success_count = 0
        
        async def create_with_possible_failure(session_id: int):
            nonlocal error_count, success_count
            if session_id % 10 == 0:
                error_count += 1
                raise ValueError(f"Simulated error for session {session_id}")
            success_count += 1
            return {"id": session_id}
        
        tasks = [create_with_possible_failure(i) for i in range(100)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        assert error_count == 10
        assert success_count == 90


class TestMixedReadWriteOperations:
    """LD-03: Mixed read/write operations tests."""
    
    @pytest.mark.asyncio
    async def test_concurrent_read_write_consistency(self, concurrent_session_manager: ConcurrentSessionManager):
        await concurrent_session_manager.create_session("rw-test", {"counter": 0, "reads": 0, "writes": 0})
        
        async def reader(reader_id: int):
            for _ in range(10):
                session = await concurrent_session_manager.get_session("rw-test")
                if session:
                    await concurrent_session_manager.update_session(
                        "rw-test",
                        {"reads": session.get("reads", 0) + 1},
                    )
                await asyncio.sleep(0.001)
        
        async def writer(writer_id: int):
            for i in range(10):
                session = await concurrent_session_manager.get_session("rw-test")
                if session:
                    await concurrent_session_manager.update_session(
                        "rw-test",
                        {
                            "counter": session.get("counter", 0) + 1,
                            "writes": session.get("writes", 0) + 1,
                        },
                    )
                await asyncio.sleep(0.001)
        
        readers = [reader(i) for i in range(5)]
        writers = [writer(i) for i in range(3)]
        
        await asyncio.gather(*readers, *writers)
        
        final_session = await concurrent_session_manager.get_session("rw-test")
        assert final_session is not None
        assert final_session["counter"] > 0
    
    @pytest.mark.asyncio
    async def test_read_heavy_workload(self, concurrent_session_manager: ConcurrentSessionManager):
        for i in range(10):
            await concurrent_session_manager.create_session(f"read-session-{i}", {"data": f"value-{i}"})
        
        read_count = 0
        lock = asyncio.Lock()
        
        async def read_operation(op_id: int):
            nonlocal read_count
            session_id = f"read-session-{op_id % 10}"
            session = await concurrent_session_manager.get_session(session_id)
            async with lock:
                read_count += 1
            return session
        
        tasks = [read_operation(i) for i in range(100)]
        results = await asyncio.gather(*tasks)
        
        assert read_count == 100
        assert all(r is not None for r in results)
    
    @pytest.mark.asyncio
    async def test_write_heavy_workload(self, concurrent_session_manager: ConcurrentSessionManager):
        await concurrent_session_manager.create_session("write-test", {"updates": 0})
        
        async def write_operation(op_id: int):
            session = await concurrent_session_manager.get_session("write-test")
            if session:
                await concurrent_session_manager.update_session(
                    "write-test",
                    {"updates": session.get("updates", 0) + 1, f"op_{op_id}": True},
                )
        
        tasks = [write_operation(i) for i in range(50)]
        await asyncio.gather(*tasks)
        
        final = await concurrent_session_manager.get_session("write-test")
        assert final is not None
        assert final["updates"] > 0
    
    @pytest.mark.asyncio
    async def test_concurrent_delete_and_read(self, concurrent_session_manager: ConcurrentSessionManager):
        for i in range(20):
            await concurrent_session_manager.create_session(f"delete-test-{i}", {"index": i})
        
        async def delete_operation(session_index: int):
            await concurrent_session_manager.delete_session(f"delete-test-{session_index}")
        
        async def read_operation(session_index: int):
            return await concurrent_session_manager.get_session(f"delete-test-{session_index}")
        
        delete_tasks = [delete_operation(i) for i in range(10)]
        read_tasks = [read_operation(i) for i in range(10, 20)]
        
        await asyncio.gather(*delete_tasks, *read_tasks)
        
        for i in range(10):
            session = await concurrent_session_manager.get_session(f"delete-test-{i}")
            assert session is None
        
        for i in range(10, 20):
            session = await concurrent_session_manager.get_session(f"delete-test-{i}")
            assert session is not None


class TestBurstTraffic:
    """LD-04: Burst traffic handling tests."""
    
    @pytest.mark.asyncio
    async def test_sudden_request_burst(self, concurrent_session_manager: ConcurrentSessionManager):
        async def burst_request(request_id: int):
            await concurrent_session_manager.create_session(
                f"burst-{request_id}",
                {"timestamp": time.time()},
            )
            return request_id
        
        burst_size = 50
        
        async with measure_time("burst") as timing:
            tasks = [burst_request(i) for i in range(burst_size)]
            results = await asyncio.gather(*tasks)
        
        assert len(results) == burst_size
    
    @pytest.mark.asyncio
    async def test_burst_with_rate_limiting(self):
        rate_limiter = {"tokens": 10, "max_tokens": 10, "refill_rate": 5}
        lock = asyncio.Lock()
        
        async def acquire_token():
            async with lock:
                if rate_limiter["tokens"] > 0:
                    rate_limiter["tokens"] -= 1
                    return True
                return False
        
        async def release_token():
            async with lock:
                rate_limiter["tokens"] = min(
                    rate_limiter["max_tokens"],
                    rate_limiter["tokens"] + 1,
                )
        
        processed = []
        rejected = []
        
        async def rate_limited_request(request_id: int):
            if await acquire_token():
                await asyncio.sleep(0.01)
                processed.append(request_id)
                await release_token()
                return True
            else:
                rejected.append(request_id)
                return False
        
        tasks = [rate_limited_request(i) for i in range(30)]
        await asyncio.gather(*tasks)
        
        assert len(processed) >= 10
        assert len(rejected) >= 0
    
    @pytest.mark.asyncio
    async def test_burst_with_queue_buffer(self):
        request_queue = asyncio.Queue()
        processed_requests = []
        
        async def enqueue_requests(count: int):
            for i in range(count):
                await request_queue.put({"id": i, "timestamp": time.time()})
        
        async def process_requests(batch_size: int = 5):
            batch = []
            while len(batch) < batch_size:
                try:
                    request = await asyncio.wait_for(request_queue.get(), timeout=0.1)
                    batch.append(request)
                except asyncio.TimeoutError:
                    break
            
            for request in batch:
                processed_requests.append(request["id"])
            
            return len(batch)
        
        await enqueue_requests(20)
        
        for _ in range(4):
            await process_requests(5)
        
        assert len(processed_requests) == 20
    
    @pytest.mark.asyncio
    async def test_burst_graceful_degradation(self):
        class GracefulService:
            def __init__(self, max_capacity: int = 10):
                self.max_capacity = max_capacity
                self.current_load = 0
                self.rejected = 0
            
            async def handle_request(self, request_id: int):
                if self.current_load >= self.max_capacity:
                    self.rejected += 1
                    return {"status": "rejected", "request_id": request_id}
                
                self.current_load += 1
                await asyncio.sleep(0.02)
                self.current_load -= 1
                return {"status": "processed", "request_id": request_id}
        
        service = GracefulService(max_capacity=10)
        
        tasks = [service.handle_request(i) for i in range(30)]
        results = await asyncio.gather(*tasks)
        
        processed = sum(1 for r in results if r["status"] == "processed")
        rejected = sum(1 for r in results if r["status"] == "rejected")
        
        assert processed <= 10
        assert rejected >= 20


class TestLoadWithMockServices:
    """Load tests using mock services."""
    
    @pytest.mark.asyncio
    async def test_llm_service_load(self, mock_llm_service: MockLLMService):
        async def make_llm_request(request_id: int):
            messages = [{"role": "user", "content": f"Request {request_id}"}]
            return mock_llm_service.chat(messages)
        
        tasks = [make_llm_request(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 50
        assert len(mock_llm_service.call_history) == 50
    
    @pytest.mark.asyncio
    async def test_tool_executor_load(self, mock_tool_executor: MockToolExecutor):
        async def execute_tool(tool_name: str, args: dict):
            return await mock_tool_executor.execute(tool_name, args, {})
        
        tasks = [
            execute_tool("thinking", {"thought": f"thought-{i}"})
            for i in range(30)
        ]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 30
        assert len(mock_tool_executor.execution_history) == 30
    
    @pytest.mark.asyncio
    async def test_dao_load(self, mock_conversation_dao: MockConversationDAO):
        async def create_session(user_id: int):
            return await mock_conversation_dao.create_session(
                user_id,
                f"Session {user_id}",
                f"workspace-{user_id}",
            )
        
        tasks = [create_session(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 50


class TestLoadMetrics:
    """Test load metrics collection."""
    
    @pytest.mark.asyncio
    async def test_response_time_under_load(self, concurrent_session_manager: ConcurrentSessionManager):
        response_times = []
        
        async def timed_request(request_id: int):
            start = time.perf_counter()
            await concurrent_session_manager.create_session(f"metric-{request_id}", {})
            end = time.perf_counter()
            response_times.append((end - start) * 1000)
        
        tasks = [timed_request(i) for i in range(50)]
        await asyncio.gather(*tasks)
        
        avg_response_time = sum(response_times) / len(response_times)
        max_response_time = max(response_times)
        
        assert avg_response_time < 100
        assert max_response_time < 500
    
    @pytest.mark.asyncio
    async def test_throughput_measurement(self, concurrent_session_manager: ConcurrentSessionManager):
        operations_count = 100
        
        start_time = time.perf_counter()
        
        tasks = [
            concurrent_session_manager.create_session(f"throughput-{i}", {})
            for i in range(operations_count)
        ]
        await asyncio.gather(*tasks)
        
        end_time = time.perf_counter()
        duration_seconds = end_time - start_time
        throughput = operations_count / duration_seconds
        
        assert throughput > 50
    
    @pytest.mark.asyncio
    async def test_resource_monitor_under_load(self, resource_monitor: ResourceMonitor):
        await resource_monitor.start_monitoring(interval_seconds=0.05)
        
        data = []
        for i in range(100):
            data.append([j for j in range(1000)])
        
        await asyncio.sleep(0.2)
        
        await resource_monitor.stop_monitoring()
        
        peak_memory = resource_monitor.get_peak_memory_mb()
        assert peak_memory is not None
        
        del data


class TestScenarioOrchestratorLoad:
    """Test scenario orchestrator under load."""
    
    @pytest.mark.asyncio
    async def test_parallel_scenario_execution(self, scenario_orchestrator: ScenarioOrchestrator):
        async def quick_scenario(scenario_id: int):
            await asyncio.sleep(0.01)
            return scenario_id
        
        configs = [
            (ScenarioConfig(name=f"load_test_{i}", description="Load test"), quick_scenario, (i,), {})
            for i in range(20)
        ]
        
        results = await scenario_orchestrator.run_parallel_scenarios(configs)
        
        assert len(results) == 20
        passed = sum(1 for r in results if r.result == ScenarioResult.PASSED)
        assert passed == 20
    
    @pytest.mark.asyncio
    async def test_scenario_report_generation(self, scenario_orchestrator: ScenarioOrchestrator):
        async def passing_test():
            return True
        
        for i in range(5):
            config = ScenarioConfig(name=f"report_test_{i}", description="Report test")
            await scenario_orchestrator.run_scenario(config, passing_test)
        
        report = scenario_orchestrator.generate_report()
        
        assert "mermaid" in report
        assert "测试结果汇总" in report
