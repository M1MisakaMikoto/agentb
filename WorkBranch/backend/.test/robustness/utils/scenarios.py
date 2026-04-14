"""
Scenario Orchestration Utilities for Robustness Testing

Provides tools for orchestrating complex test scenarios including
concurrent execution, timing control, and result collection.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum
from contextlib import asynccontextmanager


class ScenarioResult(Enum):
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class TestResult:
    test_id: str
    scenario: str
    result: ScenarioResult
    duration_ms: float
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioConfig:
    name: str
    description: str
    timeout_seconds: float = 30.0
    retry_count: int = 0
    retry_delay_seconds: float = 1.0
    expected_result: ScenarioResult = ScenarioResult.PASSED


class ScenarioOrchestrator:
    """Orchestrates test scenario execution with timing and result collection."""
    
    def __init__(self):
        self.results: List[TestResult] = []
        self._start_time: Optional[float] = None
    
    def clear_results(self):
        self.results.clear()
    
    async def run_scenario(
        self,
        config: ScenarioConfig,
        test_func: Callable,
        *args,
        **kwargs,
    ) -> TestResult:
        start_time = time.perf_counter()
        error_message = None
        result = ScenarioResult.PASSED
        details = {}
        
        for attempt in range(config.retry_count + 1):
            try:
                await asyncio.wait_for(
                    test_func(*args, **kwargs),
                    timeout=config.timeout_seconds,
                )
                result = ScenarioResult.PASSED
                break
            except asyncio.TimeoutError:
                result = ScenarioResult.TIMEOUT
                error_message = f"Scenario timed out after {config.timeout_seconds}s"
            except AssertionError as e:
                result = ScenarioResult.FAILED
                error_message = str(e)
            except Exception as e:
                result = ScenarioResult.ERROR
                error_message = f"{type(e).__name__}: {str(e)}"
            
            if attempt < config.retry_count:
                await asyncio.sleep(config.retry_delay_seconds)
        
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        test_result = TestResult(
            test_id=f"{config.name}_{len(self.results)}",
            scenario=config.name,
            result=result,
            duration_ms=duration_ms,
            error_message=error_message,
            details=details,
        )
        
        self.results.append(test_result)
        return test_result
    
    async def run_parallel_scenarios(
        self,
        scenarios: List[Tuple[ScenarioConfig, Callable, tuple, dict]],
    ) -> List[TestResult]:
        tasks = [
            self.run_scenario(config, func, *args, **kwargs)
            for config, func, args, kwargs in scenarios
        ]
        return await asyncio.gather(*tasks)
    
    def generate_report(self) -> str:
        lines = [
            "\n```mermaid",
            "flowchart TB",
            "    subgraph Results[测试结果汇总]",
        ]
        
        passed = sum(1 for r in self.results if r.result == ScenarioResult.PASSED)
        failed = sum(1 for r in self.results if r.result == ScenarioResult.FAILED)
        timeout = sum(1 for r in self.results if r.result == ScenarioResult.TIMEOUT)
        error = sum(1 for r in self.results if r.result == ScenarioResult.ERROR)
        
        lines.append(f'        Passed["✅ 通过: {passed}"]')
        lines.append(f'        Failed["❌ 失败: {failed}"]')
        lines.append(f'        Timeout["⏱️ 超时: {timeout}"]')
        lines.append(f'        Error["⚠️ 错误: {error}"]')
        lines.append("    end")
        lines.append("```")
        
        lines.append("\n### 详细结果\n")
        lines.append("| 测试ID | 场景 | 结果 | 耗时(ms) | 错误信息 |")
        lines.append("|--------|------|------|----------|----------|")
        
        for r in self.results:
            status_icon = {
                ScenarioResult.PASSED: "✅",
                ScenarioResult.FAILED: "❌",
                ScenarioResult.TIMEOUT: "⏱️",
                ScenarioResult.ERROR: "⚠️",
            }.get(r.result, "❓")
            lines.append(f"| {r.test_id} | {r.scenario} | {status_icon} {r.result.value} | {r.duration_ms:.2f} | {r.error_message or '-'} |")
        
        return "\n".join(lines)


class ConcurrentSessionManager:
    """Manages multiple concurrent test sessions."""
    
    def __init__(self, max_concurrent: int = 10):
        self.max_concurrent = max_concurrent
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
    
    async def create_session(self, session_id: str, data: Dict[str, Any] = None) -> bool:
        async with self._semaphore:
            async with self._lock:
                if session_id in self._sessions:
                    return False
                self._sessions[session_id] = data or {}
                return True
    
    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._sessions.get(session_id)
    
    async def update_session(self, session_id: str, updates: Dict[str, Any]) -> bool:
        async with self._lock:
            if session_id not in self._sessions:
                return False
            self._sessions[session_id].update(updates)
            return True
    
    async def delete_session(self, session_id: str) -> bool:
        async with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
            return True
    
    async def list_sessions(self) -> List[str]:
        async with self._lock:
            return list(self._sessions.keys())
    
    async def clear_all(self):
        async with self._lock:
            self._sessions.clear()


class ResourceMonitor:
    """Monitors resource usage during tests."""
    
    def __init__(self):
        self._snapshots: List[Dict[str, Any]] = []
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
    
    async def start_monitoring(self, interval_seconds: float = 0.5):
        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop(interval_seconds))
    
    async def stop_monitoring(self):
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
    
    async def _monitor_loop(self, interval: float):
        import tracemalloc
        import os
        
        tracemalloc.start()
        
        while self._monitoring:
            try:
                current, peak = tracemalloc.get_traced_memory()
                self._snapshots.append({
                    "timestamp": time.time(),
                    "memory_current_mb": current / 1024 / 1024,
                    "memory_peak_mb": peak / 1024 / 1024,
                })
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
        
        tracemalloc.stop()
    
    def get_peak_memory_mb(self) -> Optional[float]:
        if not self._snapshots:
            return None
        return max(s["memory_peak_mb"] for s in self._snapshots)
    
    def get_current_memory_mb(self) -> Optional[float]:
        if not self._snapshots:
            return None
        return self._snapshots[-1]["memory_current_mb"]
    
    def get_memory_trend(self) -> List[float]:
        return [s["memory_current_mb"] for s in self._snapshots]
    
    def clear(self):
        self._snapshots.clear()


@asynccontextmanager
async def measure_time(name: str = "operation"):
    """Context manager to measure execution time."""
    start = time.perf_counter()
    result = {"name": name, "duration_ms": 0}
    try:
        yield result
    finally:
        result["duration_ms"] = (time.perf_counter() - start) * 1000


async def run_with_timeout(
    coro,
    timeout_seconds: float,
    on_timeout: Optional[Callable] = None,
) -> Tuple[Any, bool]:
    """Run a coroutine with timeout, returning (result, timed_out)."""
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        return result, False
    except asyncio.TimeoutError:
        if on_timeout:
            await on_timeout()
        return None, True


async def gather_with_concurrency(
    n: int,
    *coros,
    return_exceptions: bool = True,
) -> List[Any]:
    """Run coroutines with limited concurrency."""
    semaphore = asyncio.Semaphore(n)
    
    async def run_with_semaphore(coro):
        async with semaphore:
            return await coro
    
    return await asyncio.gather(
        *[run_with_semaphore(coro) for coro in coros],
        return_exceptions=return_exceptions,
    )


def create_test_messages(count: int, base_content: str = "Test message") -> List[Dict[str, str]]:
    """Create a list of test messages."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{base_content} {i}"}
        for i in range(count)
    ]


def create_large_payload(size_kb: int) -> str:
    """Create a large string payload for testing."""
    return "x" * (size_kb * 1024)
