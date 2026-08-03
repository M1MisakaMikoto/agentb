from __future__ import annotations

import asyncio
import json
import os
from typing import Dict, List, Optional

from service.session_service.canonical import Message, SegmentType


def _seq_from_stream_id(stream_id: str) -> int:
    milliseconds, ordinal = stream_id.split("-", 1)
    ordinal_value = int(ordinal)
    if ordinal_value >= 1000:
        raise RuntimeError("Redis stream produced more than 999 events in one millisecond")
    # Remains below JavaScript's safe integer limit for current epoch values.
    return int(milliseconds) * 1000 + ordinal_value


def _stream_id_from_seq(seq: int) -> str:
    return f"{seq // 1000}-{seq % 1000}"


def _key(prefix: str, suffix: str) -> str:
    normalized = prefix.strip() or "agentb:dev:"
    if not normalized.endswith(":"):
        normalized += ":"
    return f"{normalized}{suffix}"


class RedisSubscription(asyncio.Queue):
    def __init__(self) -> None:
        super().__init__()
        self.reader_task: Optional[asyncio.Task] = None


class RedisStreamMessageQueue:
    """Redis Streams-backed message bus with resumable per-conversation streams."""

    def __init__(
        self,
        redis_url: str,
        prefix: str = "agentb:dev:",
        max_size: int = 1000,
        retention_seconds: int = 86400,
    ) -> None:
        try:
            from redis import Redis
            from redis.asyncio import Redis as AsyncRedis
        except ImportError as exc:
            raise RuntimeError("redis package is required for Redis Streams") from exc

        self._sync = Redis.from_url(redis_url, decode_responses=True)
        self._async = AsyncRedis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix
        self._max_size = max(100, max_size)
        self._retention_seconds = max(60, retention_seconds)
        self._subscriptions: Dict[str, List[RedisSubscription]] = {}
        self._running = False

    def _stream_key(self, conversation_id: str) -> str:
        return _key(self._prefix, f"stream:conversation:{conversation_id}")

    def _state_key(self, conversation_id: str) -> str:
        return _key(self._prefix, f"stream-state:conversation:{conversation_id}")

    @staticmethod
    def _fields(message: Message) -> dict[str, str]:
        return {
            "role": message.role,
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "session_id": str(message.session_id or ""),
            "workspace_id": str(message.workspace_id or ""),
            "type": message.type.value,
            "content": str(message.content or ""),
            "timestamp": message.timestamp.isoformat(),
            "metadata": json.dumps(message.metadata or {}, ensure_ascii=False),
        }

    @staticmethod
    def _message(fields: dict[str, str]) -> Message:
        return Message.from_dict(
            {
                **fields,
                "metadata": json.loads(fields.get("metadata") or "{}"),
            }
        )

    def _silent_mode(self, conversation_id: str) -> bool:
        return self._sync.hget(self._state_key(conversation_id), "silent_mode") == "1"

    def publish_sync(self, message: Message) -> bool:
        allowed_in_silent_mode = {
            SegmentType.DONE,
            SegmentType.ERROR,
            SegmentType.CANCELLED,
            SegmentType.CONVERSATION_HANDOFF,
        }
        try:
            if self._silent_mode(message.conversation_id) and message.type not in allowed_in_silent_mode:
                return True
            stream_key = self._stream_key(message.conversation_id)
            state_key = self._state_key(message.conversation_id)
            stream_id = self._sync.xadd(
                stream_key,
                self._fields(message),
                maxlen=self._max_size,
                approximate=True,
            )
            state_mapping = {
                "last_seq": str(_seq_from_stream_id(stream_id)),
                "session_id": str(message.session_id or ""),
                "workspace_id": str(message.workspace_id or ""),
            }
            if message.type in {SegmentType.DONE, SegmentType.ERROR, SegmentType.CANCELLED}:
                state_mapping["is_completed"] = "1"
            self._sync.hset(state_key, mapping=state_mapping)
            self._sync.expire(stream_key, self._retention_seconds)
            self._sync.expire(state_key, self._retention_seconds)
            return True
        except Exception:
            return False

    async def publish(self, message: Message) -> bool:
        return await asyncio.to_thread(self.publish_sync, message)

    def get_messages_after(self, conversation_id: str, last_seq: int) -> List[dict]:
        stream_key = self._stream_key(conversation_id)
        start = f"({_stream_id_from_seq(last_seq)}" if last_seq else "-"
        rows = self._sync.xrange(stream_key, min=start, max="+")
        messages = []
        for stream_id, fields in rows:
            message = self._message(fields)
            payload = message.to_dict()
            payload["seq"] = _seq_from_stream_id(stream_id)
            messages.append(payload)
        return messages

    def get_stream_state(self, conversation_id: str) -> dict:
        state = self._sync.hgetall(self._state_key(conversation_id))
        if not state:
            return {
                "last_seq": 0,
                "is_completed": False,
                "session_id": "",
                "workspace_id": "",
            }
        return {
            "last_seq": int(state.get("last_seq") or 0),
            "is_completed": state.get("is_completed") == "1",
            "session_id": state.get("session_id", ""),
            "workspace_id": state.get("workspace_id", ""),
        }

    def register_stream(
        self,
        conversation_id: str,
        session_id: str,
        workspace_id: str,
        silent_mode: bool = False,
    ) -> None:
        state_key = self._state_key(conversation_id)
        self._sync.hset(
            state_key,
            mapping={
                "session_id": str(session_id),
                "workspace_id": str(workspace_id),
                "silent_mode": "1" if silent_mode else "0",
                "is_completed": "0",
            },
        )
        self._sync.expire(state_key, self._retention_seconds)

    def subscribe(self, conversation_id: str, last_seq: int = 0) -> RedisSubscription:
        subscription = RedisSubscription()
        stream_key = self._stream_key(conversation_id)
        if last_seq:
            start_id = _stream_id_from_seq(last_seq)
        else:
            latest = self._sync.xrevrange(stream_key, count=1)
            start_id = latest[0][0] if latest else "0-0"
        subscription.reader_task = asyncio.create_task(
            self._read_stream(stream_key, start_id, subscription),
            name=f"redis-stream-{conversation_id}",
        )
        self._subscriptions.setdefault(conversation_id, []).append(subscription)
        return subscription

    async def _read_stream(
        self, stream_key: str, start_id: str, subscription: RedisSubscription
    ) -> None:
        cursor = start_id
        while True:
            try:
                batches = await self._async.xread({stream_key: cursor}, block=1000, count=100)
                for _, entries in batches:
                    for stream_id, fields in entries:
                        cursor = stream_id
                        await subscription.put(
                            (self._message(fields), _seq_from_stream_id(stream_id))
                        )
            except asyncio.CancelledError:
                return

    def unsubscribe(self, conversation_id: str, subscriber_queue: asyncio.Queue) -> None:
        subscriptions = self._subscriptions.get(conversation_id, [])
        self._subscriptions[conversation_id] = [
            item for item in subscriptions if item is not subscriber_queue
        ]
        if not self._subscriptions[conversation_id]:
            self._subscriptions.pop(conversation_id, None)
        task = getattr(subscriber_queue, "reader_task", None)
        if task is not None:
            task.cancel()

    async def start_consumer(self) -> None:
        if not self._running:
            await self._async.ping()
            self._running = True

    async def stop_consumer(self) -> None:
        self._running = False
        tasks = [
            item.reader_task
            for subscriptions in self._subscriptions.values()
            for item in subscriptions
            if item.reader_task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._subscriptions.clear()
        await self._async.aclose()
        self._sync.close()

    def close(self) -> None:
        self._sync.close()

    @property
    def size(self) -> int:
        return 0

    @property
    def is_running(self) -> bool:
        return self._running


MessageQueue = RedisStreamMessageQueue
