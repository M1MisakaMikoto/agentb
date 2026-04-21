#!/usr/bin/env python3
"""
HybridMessageQueue 单元测试

测试覆盖:
1. 消息发布与订阅
2. 断点续传
3. SQLite 持久化
4. 自动清理
5. 序号追踪
6. 并发安全
"""

import asyncio
import gc
import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from service.session_service.mq import HybridMessageQueue, StreamState
from service.session_service.canonical import Message, SegmentType, MessageBuilder


@pytest.fixture
def temp_db_path(tmp_path):
    db_path = tmp_path / "test_mq.db"
    yield str(db_path)


@pytest.fixture
def mq(temp_db_path):
    queue = HybridMessageQueue(db_path=temp_db_path, max_size=100)
    yield queue
    queue.close()
    gc.collect()


@pytest.fixture
def sample_message():
    return Message(
        role="assistant",
        message_id="msg-001",
        conversation_id="conv-001",
        session_id="session-001",
        workspace_id="ws-001",
        type=SegmentType.TEXT_DELTA,
        content="Hello World",
        metadata={"key": "value"}
    )


class TestMessagePublish:
    """消息发布测试"""
    
    def test_publish_sync_stores_to_sqlite(self, mq, sample_message):
        result = mq.publish_sync(sample_message)
        
        assert result is True
        
        messages = mq.get_messages_after("conv-001", 0)
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello World"
        assert messages[0]["seq"] == 1
    
    def test_publish_sync_increments_seq(self, mq, sample_message):
        mq.publish_sync(sample_message)
        
        msg2 = Message(
            role="assistant",
            message_id="msg-002",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content="Second message"
        )
        mq.publish_sync(msg2)
        
        messages = mq.get_messages_after("conv-001", 0)
        assert len(messages) == 2
        assert messages[0]["seq"] == 1
        assert messages[1]["seq"] == 2
    
    def test_publish_sync_different_conversations(self, mq, sample_message):
        mq.publish_sync(sample_message)
        
        msg2 = Message(
            role="assistant",
            message_id="msg-002",
            conversation_id="conv-002",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content="Another conversation"
        )
        mq.publish_sync(msg2)
        
        messages1 = mq.get_messages_after("conv-001", 0)
        messages2 = mq.get_messages_after("conv-002", 0)
        
        assert len(messages1) == 1
        assert len(messages2) == 1
        assert messages1[0]["seq"] == 1
        assert messages2[0]["seq"] == 1
    
    @pytest.mark.asyncio
    async def test_publish_async(self, mq, sample_message):
        result = await mq.publish(sample_message)
        
        assert result is True
        
        messages = mq.get_messages_after("conv-001", 0)
        assert len(messages) == 1


class TestMessageSubscription:
    """消息订阅测试"""
    
    @pytest.mark.asyncio
    async def test_subscribe_receives_messages(self, mq, sample_message):
        await mq.start_consumer()
        
        subscriber = mq.subscribe("conv-001")
        mq.publish_sync(sample_message)
        
        await asyncio.sleep(0.1)
        
        message, seq = await asyncio.wait_for(subscriber.get(), timeout=1.0)
        
        assert message.content == "Hello World"
        assert seq == 1
        
        await mq.stop_consumer()
    
    @pytest.mark.asyncio
    async def test_unsubscribe_stops_receiving(self, mq, sample_message):
        await mq.start_consumer()
        
        subscriber = mq.subscribe("conv-001")
        mq.unsubscribe("conv-001", subscriber)
        
        mq.publish_sync(sample_message)
        
        await asyncio.sleep(0.1)
        
        assert subscriber.empty()
        
        await mq.stop_consumer()
    
    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, mq, sample_message):
        await mq.start_consumer()
        
        sub1 = mq.subscribe("conv-001")
        sub2 = mq.subscribe("conv-001")
        
        mq.publish_sync(sample_message)
        
        await asyncio.sleep(0.1)
        
        msg1, seq1 = await asyncio.wait_for(sub1.get(), timeout=1.0)
        msg2, seq2 = await asyncio.wait_for(sub2.get(), timeout=1.0)
        
        assert msg1.content == "Hello World"
        assert msg2.content == "Hello World"
        assert seq1 == seq2 == 1
        
        await mq.stop_consumer()


class TestResumeFromBreakpoint:
    """断点续传测试"""
    
    def test_get_messages_after_returns_correct_range(self, mq, sample_message):
        for i in range(5):
            msg = Message(
                role="assistant",
                message_id=f"msg-{i:03d}",
                conversation_id="conv-001",
                session_id="session-001",
                workspace_id="ws-001",
                type=SegmentType.TEXT_DELTA,
                content=f"Message {i}"
            )
            mq.publish_sync(msg)
        
        messages = mq.get_messages_after("conv-001", 2)
        
        assert len(messages) == 3
        assert messages[0]["seq"] == 3
        assert messages[0]["content"] == "Message 2"
        assert messages[2]["seq"] == 5
        assert messages[2]["content"] == "Message 4"
    
    def test_get_messages_after_empty_for_new_conversation(self, mq):
        messages = mq.get_messages_after("non-existent-conv", 0)
        
        assert len(messages) == 0
    
    @pytest.mark.asyncio
    async def test_subscribe_with_last_seq_receives_missed_messages(self, mq):
        for i in range(3):
            msg = Message(
                role="assistant",
                message_id=f"msg-{i:03d}",
                conversation_id="conv-001",
                session_id="session-001",
                workspace_id="ws-001",
                type=SegmentType.TEXT_DELTA,
                content=f"Message {i}"
            )
            mq.publish_sync(msg)

        subscriber = mq.subscribe("conv-001", last_seq=1)

        missed = []
        while not subscriber.empty():
            msg, seq = subscriber.get_nowait()
            missed.append((msg, seq))

        assert len(missed) == 2
        assert missed[0][1] == 2
        assert missed[1][1] == 3


class TestAutoCleanup:
    """自动清理测试"""
    
    def test_cleanup_on_done(self, mq, sample_message):
        mq.publish_sync(sample_message)
        
        done_msg = Message(
            role="assistant",
            message_id="msg-done",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.DONE,
            content=""
        )
        mq.publish_sync(done_msg)
        
        messages = mq.get_messages_after("conv-001", 0)
        
        assert len(messages) == 0
    
    def test_cleanup_clears_stream_state(self, mq, sample_message):
        mq.publish_sync(sample_message)
        
        state_before = mq.get_stream_state("conv-001")
        assert state_before["last_seq"] == 1
        
        done_msg = Message(
            role="assistant",
            message_id="msg-done",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.DONE,
            content=""
        )
        mq.publish_sync(done_msg)
        
        state_after = mq.get_stream_state("conv-001")
        assert state_after["last_seq"] == 0
    
    def test_other_conversations_not_affected(self, mq, sample_message):
        mq.publish_sync(sample_message)
        
        msg2 = Message(
            role="assistant",
            message_id="msg-002",
            conversation_id="conv-002",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content="Another conversation"
        )
        mq.publish_sync(msg2)
        
        done_msg = Message(
            role="assistant",
            message_id="msg-done",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.DONE,
            content=""
        )
        mq.publish_sync(done_msg)
        
        messages1 = mq.get_messages_after("conv-001", 0)
        messages2 = mq.get_messages_after("conv-002", 0)
        
        assert len(messages1) == 0
        assert len(messages2) == 1


class TestStreamState:
    """流状态测试"""
    
    def test_get_stream_state_for_new_conversation(self, mq):
        state = mq.get_stream_state("non-existent-conv")
        
        assert state["last_seq"] == 0
        assert state["is_completed"] is False
        assert state["session_id"] == ""
        assert state["workspace_id"] == ""
    
    def test_get_stream_state_after_messages(self, mq, sample_message):
        mq.publish_sync(sample_message)
        
        state = mq.get_stream_state("conv-001")
        
        assert state["last_seq"] == 1
        assert state["is_completed"] is False
    
    def test_get_stream_state_after_done(self, mq, sample_message):
        mq.publish_sync(sample_message)
        
        done_msg = Message(
            role="assistant",
            message_id="msg-done",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.DONE,
            content=""
        )
        mq.publish_sync(done_msg)
        
        state = mq.get_stream_state("conv-001")
        
        assert state["is_completed"] is True
    
    def test_register_stream(self, mq):
        mq.register_stream(
            conversation_id="conv-new",
            session_id="session-001",
            workspace_id="ws-001"
        )
        
        state = mq.get_stream_state("conv-new")
        
        assert state["session_id"] == "session-001"
        assert state["workspace_id"] == "ws-001"


class TestConcurrency:
    """并发安全测试"""
    
    def test_concurrent_publish_sync(self, mq):
        num_messages = 50
        threads = []
        errors = []
        
        def publish_messages(thread_id):
            try:
                for i in range(num_messages):
                    msg = Message(
                        role="assistant",
                        message_id=f"msg-{thread_id}-{i}",
                        conversation_id="conv-001",
                        session_id="session-001",
                        workspace_id="ws-001",
                        type=SegmentType.TEXT_DELTA,
                        content=f"Thread {thread_id} Message {i}"
                    )
                    mq.publish_sync(msg)
            except Exception as e:
                errors.append(e)
        
        for t_id in range(5):
            t = threading.Thread(target=publish_messages, args=(t_id,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        
        messages = mq.get_messages_after("conv-001", 0)
        assert len(messages) == 250
        
        seqs = [m["seq"] for m in messages]
        assert len(seqs) == len(set(seqs))
    
    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe(self, mq):
        await mq.start_consumer()
        
        subscribers = []
        
        for _ in range(10):
            sub = mq.subscribe("conv-001")
            subscribers.append(sub)
        
        for sub in subscribers[:5]:
            mq.unsubscribe("conv-001", sub)
        
        msg = Message(
            role="assistant",
            message_id="msg-001",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content="Test"
        )
        mq.publish_sync(msg)
        
        await asyncio.sleep(0.1)
        
        for sub in subscribers[5:]:
            assert not sub.empty()
        
        for sub in subscribers[:5]:
            assert sub.empty()
        
        await mq.stop_consumer()


class TestEdgeCases:
    """边界条件测试"""
    
    def test_empty_content_message(self, mq):
        msg = Message(
            role="assistant",
            message_id="msg-001",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content=""
        )
        
        result = mq.publish_sync(msg)
        
        assert result is True
        
        messages = mq.get_messages_after("conv-001", 0)
        assert len(messages) == 1
        assert messages[0]["content"] == ""
    
    def test_large_content_message(self, mq):
        large_content = "x" * 100000
        
        msg = Message(
            role="assistant",
            message_id="msg-001",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content=large_content
        )
        
        result = mq.publish_sync(msg)
        
        assert result is True
        
        messages = mq.get_messages_after("conv-001", 0)
        assert len(messages) == 1
        assert len(messages[0]["content"]) == 100000
    
    def test_unicode_content(self, mq):
        unicode_content = "你好世界 🌍 مرحبا"
        
        msg = Message(
            role="assistant",
            message_id="msg-001",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content=unicode_content
        )
        
        result = mq.publish_sync(msg)
        
        assert result is True
        
        messages = mq.get_messages_after("conv-001", 0)
        assert messages[0]["content"] == unicode_content
    
    def test_metadata_with_special_characters(self, mq):
        msg = Message(
            role="assistant",
            message_id="msg-001",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=SegmentType.TEXT_DELTA,
            content="test",
            metadata={"key": "value with \"quotes\" and \n newlines"}
        )
        
        result = mq.publish_sync(msg)
        
        assert result is True
        
        messages = mq.get_messages_after("conv-001", 0)
        assert "quotes" in messages[0]["metadata"]["key"]


class TestMessageTypes:
    """不同消息类型测试"""
    
    @pytest.mark.parametrize("msg_type", [
        SegmentType.TEXT_START,
        SegmentType.TEXT_DELTA,
        SegmentType.TEXT_END,
        SegmentType.THINKING_START,
        SegmentType.THINKING_DELTA,
        SegmentType.THINKING_END,
        SegmentType.TOOL_CALL,
        SegmentType.TOOL_RES,
        SegmentType.ERROR,
        SegmentType.STATE_CHANGE,
    ])
    def test_different_message_types(self, mq, msg_type):
        msg = Message(
            role="assistant",
            message_id="msg-001",
            conversation_id="conv-001",
            session_id="session-001",
            workspace_id="ws-001",
            type=msg_type,
            content="test content"
        )
        
        result = mq.publish_sync(msg)
        
        assert result is True
        
        messages = mq.get_messages_after("conv-001", 0)
        assert messages[0]["type"] == msg_type.value


class TestQueueProperties:
    """队列属性测试"""
    
    def test_size_property(self, mq):
        assert mq.size == 0
    
    def test_is_running_property(self, mq):
        assert mq.is_running is False
        
        asyncio.run(mq.start_consumer())
        
        assert mq.is_running is True
        
        asyncio.run(mq.stop_consumer())
        
        assert mq.is_running is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
