from __future__ import annotations

import asyncio
import os
import re
import socket
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional


AFFINITY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AffinityError(RuntimeError):
    pass


class OwnerConflictError(AffinityError):
    def __init__(self, session_id: str, owner_instance_id: str):
        super().__init__(
            f"Session {session_id} is owned by instance {owner_instance_id}"
        )
        self.session_id = session_id
        self.owner_instance_id = owner_instance_id


def validate_affinity_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key:
        raise AffinityError("X-AgentB-Affinity-Key is required")
    if not AFFINITY_KEY_PATTERN.fullmatch(key):
        raise AffinityError("X-AgentB-Affinity-Key has an invalid format")
    return key


def _redis_key(prefix: str, suffix: str) -> str:
    normalized = prefix.strip() or "agentb:dev:"
    if not normalized.endswith(":"):
        normalized += ":"
    return f"{normalized}{suffix}"


@dataclass(frozen=True)
class OwnerClaim:
    session_id: str
    instance_id: str
    acquired: bool


class RedisOwnerCoordinator:
    _RENEW_SCRIPT = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('EXPIRE', KEYS[1], ARGV[2])
    end
    return 0
    """

    _RELEASE_SCRIPT = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    end
    return 0
    """

    def __init__(self, redis_url: str, prefix: str, instance_id: str, lease_seconds: int):
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError(
                "redis package is required when AGENTB_REDIS_URL is configured"
            ) from exc

        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix
        self._instance_id = instance_id
        self._lease_seconds = max(10, lease_seconds)
        self._owned_sessions: set[str] = set()
        self._owned_lock = asyncio.Lock()

    def _owner_key(self, session_id: str) -> str:
        return _redis_key(self._prefix, f"owner:session:{session_id}")

    def _heartbeat_key(self) -> str:
        return _redis_key(self._prefix, f"instance:{self._instance_id}")

    async def ping(self) -> None:
        await self._redis.ping()

    async def heartbeat(self, draining: bool, active_tasks: int) -> None:
        key = self._heartbeat_key()
        await self._redis.hset(
            key,
            mapping={
                "instance_id": self._instance_id,
                "draining": "1" if draining else "0",
                "active_tasks": str(active_tasks),
            },
        )
        await self._redis.expire(key, self._lease_seconds)

        async with self._owned_lock:
            session_ids = tuple(self._owned_sessions)
        for session_id in session_ids:
            renewed = await self._redis.eval(
                self._RENEW_SCRIPT,
                1,
                self._owner_key(session_id),
                self._instance_id,
                self._lease_seconds,
            )
            if not renewed:
                async with self._owned_lock:
                    self._owned_sessions.discard(session_id)

    async def claim(self, session_id: str) -> OwnerClaim:
        key = self._owner_key(session_id)
        acquired = await self._redis.set(
            key,
            self._instance_id,
            nx=True,
            ex=self._lease_seconds,
        )
        if not acquired:
            current_owner = await self._redis.get(key)
            if current_owner != self._instance_id:
                raise OwnerConflictError(session_id, current_owner or "unknown")
            await self._redis.expire(key, self._lease_seconds)

        async with self._owned_lock:
            self._owned_sessions.add(session_id)
        return OwnerClaim(session_id, self._instance_id, bool(acquired))

    async def release(self, session_id: str) -> None:
        await self._redis.eval(
            self._RELEASE_SCRIPT,
            1,
            self._owner_key(session_id),
            self._instance_id,
        )
        async with self._owned_lock:
            self._owned_sessions.discard(session_id)

    async def close(self) -> None:
        await self._redis.aclose()


class RuntimeState:
    def __init__(self) -> None:
        self.instance_id = os.getenv("AGENTB_INSTANCE_ID", "").strip() or socket.gethostname()
        self.redis_url = os.getenv("AGENTB_REDIS_URL", "").strip()
        self.redis_prefix = os.getenv("AGENTB_REDIS_PREFIX", "agentb:dev:").strip()
        self.lease_seconds = int(os.getenv("AGENTB_OWNER_LEASE_SECONDS", "60"))
        self.heartbeat_seconds = int(os.getenv("AGENTB_HEARTBEAT_SECONDS", "15"))
        self.drain_timeout_seconds = int(os.getenv("AGENTB_DRAIN_TIMEOUT_SECONDS", "30"))
        self.draining = False
        self._active_sessions: Counter[str] = Counter()
        self._active_lock = asyncio.Lock()
        self._coordinator: Optional[RedisOwnerCoordinator] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url)

    @property
    def active_task_count(self) -> int:
        return sum(self._active_sessions.values())

    @property
    def active_session_count(self) -> int:
        return len(self._active_sessions)

    async def start(self) -> None:
        self.draining = False
        if not self.redis_url:
            return
        self._coordinator = RedisOwnerCoordinator(
            self.redis_url,
            self.redis_prefix,
            self.instance_id,
            self.lease_seconds,
        )
        await self._coordinator.ping()
        await self._send_heartbeat()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="agentb-instance-heartbeat"
        )

    async def _send_heartbeat(self) -> None:
        if self._coordinator is not None:
            await self._coordinator.heartbeat(self.draining, self.active_task_count)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(max(1, self.heartbeat_seconds))
                await self._send_heartbeat()
            except asyncio.CancelledError:
                return
            except Exception:
                # A request-side claim will surface Redis failures to callers.
                continue

    async def claim_session(self, session_id: int | str) -> OwnerClaim:
        normalized = validate_affinity_key(str(session_id))
        if self._coordinator is None:
            return OwnerClaim(normalized, self.instance_id, False)
        return await self._coordinator.claim(normalized)

    async def release_session(self, session_id: int | str) -> None:
        if self._coordinator is not None:
            await self._coordinator.release(str(session_id))

    @asynccontextmanager
    async def active_session(self, session_id: int | str) -> AsyncIterator[None]:
        normalized = str(session_id)
        async with self._active_lock:
            self._active_sessions[normalized] += 1
        try:
            yield
        finally:
            async with self._active_lock:
                self._active_sessions[normalized] -= 1
                if self._active_sessions[normalized] <= 0:
                    del self._active_sessions[normalized]

    async def begin_drain(self) -> None:
        self.draining = True
        try:
            await self._send_heartbeat()
        except Exception:
            # Local draining must still work while Redis is unavailable.
            pass

    async def wait_for_drain(self) -> bool:
        deadline = asyncio.get_running_loop().time() + self.drain_timeout_seconds
        while self.active_task_count and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
        return self.active_task_count == 0

    async def stop(self) -> None:
        await self.begin_drain()
        await self.wait_for_drain()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        if self._coordinator is not None:
            await self._coordinator.close()
            self._coordinator = None


_runtime_state = RuntimeState()


def get_runtime_state() -> RuntimeState:
    return _runtime_state
