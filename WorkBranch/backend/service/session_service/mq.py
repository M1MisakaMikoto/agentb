import asyncio
import json
import sqlite3
import threading
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.logging import bind_ctx
from service.session_service.canonical import Message, SegmentType


@dataclass
class StreamState:
    conversation_id: str
    last_seq: int = 0
    is_completed: bool = False
    session_id: str = ""
    workspace_id: str = ""
    silent_mode: bool = False  # 静默模式：过滤流式delta事件，仅保留heartbeat/done/error


class HybridMessageQueue:
    """混合消息队列：内存实时推送 + SQLite持久化（断点续传）"""

    def __init__(self, db_path: str = "data/db/mq.db", max_size: int = 1000):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_size = max_size

        self._async_queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._sync_queue: queue.Queue = queue.Queue(maxsize=max_size)

        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._subscribers_lock = threading.Lock()

        self._stream_states: Dict[str, StreamState] = {}
        self._stream_states_lock = threading.Lock()

        self._consumer_task: Optional[asyncio.Task] = None
        self._running = False

        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._sync_bridge_running = False
        self._sync_bridge_thread: Optional[threading.Thread] = None

        self._logger = None
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS message_stream (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    session_id TEXT,
                    workspace_id TEXT,
                    message_type TEXT NOT NULL,
                    content TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(conversation_id, seq)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_seq "
                "ON message_stream(conversation_id, seq)"
            )
            conn.commit()

    def _get_logger(self):
        if self._logger is None:
            from singleton import get_logging_runtime
            self._logger = get_logging_runtime().get_logger("mq")
        return self._logger

    def _log_event(
        self, level: str, event: str, msg: str,
        conversation_id: str = None, extra: dict = None
    ) -> None:
        ctx = {}
        if conversation_id:
            ctx["conversation_id"] = conversation_id
        with bind_ctx(**ctx):
            logger = self._get_logger()
            if level == "ERROR":
                logger.error(event=event, msg=msg, extra=extra or {})
            else:
                logger.info(event=event, msg=msg, extra=extra or {})

    def _get_next_seq(self, conversation_id: str) -> int:
        with self._stream_states_lock:
            state = self._stream_states.get(conversation_id)
            if state:
                state.last_seq += 1
                return state.last_seq

            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute(
                    "SELECT MAX(seq) FROM message_stream WHERE conversation_id = ?",
                    (conversation_id,)
                )
                result = cursor.fetchone()[0]

            max_seq = result if result else 0
            next_seq = max_seq + 1
            self._stream_states[conversation_id] = StreamState(
                conversation_id=conversation_id,
                last_seq=next_seq
            )
            return next_seq

    def _save_to_sqlite(self, message: Message, seq: int) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO message_stream
                       (conversation_id, seq, message_id, session_id, workspace_id,
                        message_type, content, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        message.conversation_id,
                        seq,
                        message.message_id,
                        message.session_id,
                        message.workspace_id,
                        message.type.value,
                        message.content,
                        json.dumps(message.metadata) if message.metadata else None
                    )
                )
                conn.commit()
        except Exception as e:
            self._log_event(
                "ERROR", "mq.sqlite.save_failed", f"SQLite save error: {e}",
                conversation_id=message.conversation_id
            )
            with self._stream_states_lock:
                if message.conversation_id in self._stream_states:
                    self._stream_states[message.conversation_id].last_seq -= 1

    def _cleanup_conversation(self, conversation_id: str) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "DELETE FROM message_stream WHERE conversation_id = ?",
                    (conversation_id,)
                )
                conn.commit()

            with self._stream_states_lock:
                if conversation_id in self._stream_states:
                    self._stream_states[conversation_id].is_completed = True
                    self._stream_states[conversation_id].last_seq = 0

            self._log_event(
                "INFO", "mq.conversation.cleaned", "Conversation messages cleaned",
                conversation_id=conversation_id
            )
        except Exception as e:
            self._log_event(
                "ERROR", "mq.cleanup.failed", f"Cleanup error: {e}",
                conversation_id=conversation_id
            )

    def publish_sync(self, message: Message) -> bool:
        try:
            import datetime as dt
            publish_timestamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')

            seq = self._get_next_seq(message.conversation_id)

            # 判断是否为流式事件（delta类型）
            is_streaming_delta = message.type.value.endswith("_delta")
            full_content = str(message.content) if message.content else ""

            # ===== 静默模式过滤逻辑（增强版）=====
            with self._stream_states_lock:
                stream_state = self._stream_states.get(message.conversation_id)
                is_silent_mode = stream_state.silent_mode if stream_state else False

            # 定义静默模式下允许通过的事件类型白名单
            SILENT_MODE_ALLOWED_TYPES = {
                "done",           # 最终结果（必须）
                "error",          # 错误信息（必须）
                "heartbeat",      # 心跳信号（保持连接）
                "conversation_handoff",  # 会话交接（可选，视业务需求）
            }

            if is_silent_mode:
                event_type = message.type.value
                is_allowed = event_type in SILENT_MODE_ALLOWED_TYPES

                if not is_allowed:
                    # 静默模式：过滤所有中间过程事件（delta + start/end/state/tool等）
                    self._log_event(
                        "DEBUG", "mq.silent_filtered",
                        f"Silent mode: filtered intermediate event {event_type}",
                        conversation_id=message.conversation_id,
                        extra={"seq": seq, "type": event_type}
                    )
                    return True  # 返回成功但不实际发布
            # ====================================

            diagnostic_log_lines = []
            diagnostic_log_lines.append(f"\n[{publish_timestamp}] === 🔍 DIAGNOSTIC: MQ PUBLISH_SYNC ===")
            diagnostic_log_lines.append(f"[{publish_timestamp}] 📤 Message details:")
            diagnostic_log_lines.append(f"  - conversation_id: {message.conversation_id}")
            diagnostic_log_lines.append(f"  - seq: {seq}")
            diagnostic_log_lines.append(f"  - type: {message.type.value}")

            if is_streaming_delta:
                # 流式事件：记录完整内容，禁止截断
                diagnostic_log_lines.append(f"  - content ({len(full_content)} chars): {full_content}")
            else:
                # 非流式事件：保持原有格式
                diagnostic_log_lines.append(f"  - content length: {len(full_content)} chars")
                diagnostic_log_lines.append(f"  - content: {full_content if full_content else '(empty)'}")

            diagnostic_log_lines.append(f"  - subscribers count: {len(self._subscribers.get(message.conversation_id, []))}")

            if is_streaming_delta:
                diagnostic_log_lines.append(f"[{publish_timestamp}] ⚠️  STREAMING_DELTA DETECTED!")
                diagnostic_log_lines.append(f"[{publish_timestamp}] 📊 This is streaming token #{seq} for this conversation")

            self._save_to_sqlite(message, seq)
            self._sync_queue.put_nowait((message, seq))

            print(f"[MQ] publish_sync: conv={message.conversation_id}, type={message.type.value}, seq={seq}, subscribers={len(self._subscribers.get(message.conversation_id, []))}")

            if message.type == SegmentType.DONE:
                with self._stream_states_lock:
                    if message.conversation_id in self._stream_states:
                        self._stream_states[message.conversation_id].is_completed = True
                self._cleanup_conversation(message.conversation_id)
                
                diagnostic_log_lines.append(f"[{publish_timestamp}] ✅ DONE signal sent, cleaning up conversation")

            with open('llm_decision_trace.log', 'a', encoding='utf-8') as f:
                f.write('\n'.join(diagnostic_log_lines) + '\n')
                f.flush()

            self._log_event(
                "INFO", "mq.message.published", "Message published",
                conversation_id=message.conversation_id,
                extra={"seq": seq, "type": message.type.value}
            )
            return True
        except Exception as e:
            self._log_event(
                "ERROR", "mq.publish.failed", f"Publish error: {e}",
                conversation_id=message.conversation_id
            )
            return False

    async def publish(self, message: Message) -> bool:
        try:
            seq = self._get_next_seq(message.conversation_id)
            self._save_to_sqlite(message, seq)
            await self._async_queue.put((message, seq))

            if message.type == SegmentType.DONE:
                with self._stream_states_lock:
                    if message.conversation_id in self._stream_states:
                        self._stream_states[message.conversation_id].is_completed = True
                self._cleanup_conversation(message.conversation_id)

            return True
        except Exception as e:
            self._log_event(
                "ERROR", "mq.publish.failed", f"Publish error: {e}",
                conversation_id=message.conversation_id
            )
            return False

    def get_messages_after(self, conversation_id: str, last_seq: int) -> List[dict]:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """SELECT seq, message_id, session_id, workspace_id,
                          message_type, content, metadata
                   FROM message_stream
                   WHERE conversation_id = ? AND seq > ?
                   ORDER BY seq ASC""",
                (conversation_id, last_seq)
            )
            rows = cursor.fetchall()

        messages = []
        for row in rows:
            seq, msg_id, session_id, workspace_id, msg_type, content, metadata = row
            messages.append({
                "seq": seq,
                "message_id": msg_id,
                "session_id": session_id or "",
                "workspace_id": workspace_id or "",
                "type": msg_type,
                "content": content or "",
                "metadata": json.loads(metadata) if metadata else {}
            })
        return messages

    def get_stream_state(self, conversation_id: str) -> dict:
        with self._stream_states_lock:
            state = self._stream_states.get(conversation_id)
            if state:
                return {
                    "last_seq": state.last_seq,
                    "is_completed": state.is_completed,
                    "session_id": state.session_id,
                    "workspace_id": state.workspace_id
                }

        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """SELECT MAX(seq), message_type, session_id, workspace_id
                   FROM message_stream
                   WHERE conversation_id = ?
                   ORDER BY seq DESC LIMIT 1""",
                (conversation_id,)
            )
            row = cursor.fetchone()

        if row and row[0]:
            return {
                "last_seq": row[0],
                "is_completed": row[1] == SegmentType.DONE.value,
                "session_id": row[2] or "",
                "workspace_id": row[3] or ""
            }

        return {
            "last_seq": 0,
            "is_completed": True,
            "session_id": "",
            "workspace_id": ""
        }

    def register_stream(
        self, conversation_id: str, session_id: str, workspace_id: str, silent_mode: bool = False
    ) -> None:
        with self._stream_states_lock:
            if conversation_id not in self._stream_states:
                self._stream_states[conversation_id] = StreamState(
                    conversation_id=conversation_id,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    silent_mode=silent_mode
                )
            else:
                self._stream_states[conversation_id].session_id = session_id
                self._stream_states[conversation_id].workspace_id = workspace_id
                self._stream_states[conversation_id].silent_mode = silent_mode

    def subscribe(
        self, conversation_id: str, last_seq: int = 0
    ) -> asyncio.Queue:
        subscriber_queue: asyncio.Queue = asyncio.Queue()

        with self._subscribers_lock:
            subscribers = self._subscribers.setdefault(conversation_id, [])
            subscribers.append(subscriber_queue)

        print(f"[MQ] subscribe: conv={conversation_id}, last_seq={last_seq}, total_subscribers={len(subscribers)}")

        if last_seq > 0:
            missed_messages = self.get_messages_after(conversation_id, last_seq)
            state = self.get_stream_state(conversation_id)
            for msg_data in missed_messages:
                message = Message(
                    role="assistant",
                    message_id=msg_data["message_id"],
                    conversation_id=conversation_id,
                    session_id=msg_data.get("session_id", state.get("session_id", "")),
                    workspace_id=msg_data.get("workspace_id", state.get("workspace_id", "")),
                    type=SegmentType(msg_data["type"]),
                    content=msg_data["content"],
                    metadata=msg_data["metadata"]
                )
                subscriber_queue.put_nowait((message, msg_data["seq"]))

        return subscriber_queue

    def unsubscribe(
        self, conversation_id: str, subscriber_queue: asyncio.Queue
    ) -> None:
        with self._subscribers_lock:
            subscribers = self._subscribers.get(conversation_id)
            if not subscribers:
                return
            self._subscribers[conversation_id] = [
                q for q in subscribers if q is not subscriber_queue
            ]
            if not self._subscribers[conversation_id]:
                del self._subscribers[conversation_id]

    def _publish_to_subscribers(self, message: Message, seq: int) -> None:
        with self._subscribers_lock:
            subscribers = list(self._subscribers.get(message.conversation_id, []))
        print(f"[MQ] _publish_to_subscribers: conv={message.conversation_id}, type={message.type.value}, seq={seq}, subscribers_count={len(subscribers)}")
        for subscriber in subscribers:
            try:
                subscriber.put_nowait((message, seq))
                print(f"[MQ] Message put into subscriber queue")
            except asyncio.QueueFull:
                print(f"[MQ] Queue full, skipping subscriber")
                continue

    async def start_consumer(self) -> None:
        if self._running:
            return

        self._running = True
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None

        self._consumer_task = asyncio.create_task(self._consume_loop())
        self._start_sync_bridge()

    def _start_sync_bridge(self) -> None:
        if self._sync_bridge_running:
            return

        self._sync_bridge_running = True
        self._sync_bridge_thread = threading.Thread(
            target=self._sync_bridge_loop,
            daemon=True
        )
        self._sync_bridge_thread.start()

    def _do_put(self, queue: asyncio.Queue, message, seq) -> None:
        """Helper function to put message into queue, called via call_soon_threadsafe"""
        try:
            queue.put_nowait((message, seq))
            print(f"[MQ Bridge] _do_put succeeded")
        except Exception as e:
            print(f"[MQ Bridge] _do_put: error {e}")
            pass

    def _sync_bridge_loop(self) -> None:
        print(f"[MQ Bridge] Starting sync bridge loop")

        while self._sync_bridge_running:
            try:
                item = self._sync_queue.get(timeout=0.1)
                message, seq = item
                print(f"[MQ Bridge] Got message: type={message.type.value}, seq={seq}")

                # 获取订阅者列表（在线程安全的方式下）
                with self._subscribers_lock:
                    subscribers = list(self._subscribers.get(message.conversation_id, []))

                if not subscribers:
                    print(f"[MQ Bridge] No subscribers for conversation {message.conversation_id}")
                    continue

                print(f"[MQ Bridge] Dispatching to {len(subscribers)} subscribers")

                # 优先使用 _main_loop（从测试的 async fixture 传入）
                # 如果 _main_loop 是测试的事件循环，通知机制应该能正常工作
                if self._main_loop:
                    print(f"[MQ Bridge] Using _main_loop for dispatch")
                    # 使用 call_soon_threadsafe 直接调度 put 操作到订阅者队列
                    for i, subscriber in enumerate(subscribers):
                        print(f"[MQ Bridge] Scheduling put for subscriber {i}")
                        self._main_loop.call_soon_threadsafe(
                            lambda s=subscriber, m=message, sn=seq:
                                self._do_put(s, m, sn)
                        )
                    print(f"[MQ Bridge] All puts scheduled")
                    continue

                # 回退方案：直接放入队列
                print(f"[MQ Bridge] Using fallback (direct put)")
                for subscriber in subscribers:
                    try:
                        subscriber.put_nowait((message, seq))
                        print(f"[MQ Bridge] Direct put succeeded")
                    except asyncio.QueueFull as e:
                        print(f"[MQ Bridge] Queue full, skipping: {e}")
                        continue
                    except Exception as e:
                        print(f"[MQ Bridge] Direct put failed: {e}")
                        continue

            except queue.Empty:
                continue
            except Exception as e:
                print(f"[MQ Bridge] Error: {e}")
                self._log_event("ERROR", "mq.bridge.error", f"Bridge error: {e}")

    async def _dispatch_to_subscribers(self, message: Message, seq: int) -> None:
        self._publish_to_subscribers(message, seq)

    async def _consume_loop(self) -> None:
        while self._running:
            try:
                message, seq = await asyncio.wait_for(
                    self._async_queue.get(),
                    timeout=1.0
                )
                self._publish_to_subscribers(message, seq)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_event("ERROR", "mq.consume.error", f"Consume error: {e}")

    async def stop_consumer(self) -> None:
        if not self._running:
            return

        self._running = False
        self._sync_bridge_running = False

        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

    def close(self) -> None:
        self._running = False
        self._sync_bridge_running = False
        self._stream_states.clear()
        with self._subscribers_lock:
            self._subscribers.clear()

    @property
    def size(self) -> int:
        return self._async_queue.qsize()

    @property
    def is_running(self) -> bool:
        return self._running


MessageQueue = HybridMessageQueue
