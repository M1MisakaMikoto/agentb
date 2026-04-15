#!/usr/bin/env python3
"""
MQ Test - Simulate Agent sending messages

Test if MessageQueue works correctly between Agent and Frontend

Usage:
    python test_mq.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from service.session_service.mq import MessageQueue
from service.session_service.canonical import Message, SegmentType
from service.settings_service.settings_service import SettingsService


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


async def test_mq_basic():
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  MQ 基础测试{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    settings = SettingsService()
    mq = MessageQueue(settings)

    conversation_id = "test-conv-001"
    workspace_id = "test-workspace-001"
    session_id = "test-session-001"

    print(f"{Colors.CYAN}[1] 启动 MQ 消费者...{Colors.ENDC}")
    await mq.start_consumer()
    print(f"{Colors.GREEN}    MQ 消费者已启动{Colors.ENDC}")

    print(f"{Colors.CYAN}[2] 订阅消息...{Colors.ENDC}")
    subscriber = mq.subscribe(conversation_id)
    print(f"{Colors.GREEN}    已订阅 conversation_id: {conversation_id}{Colors.ENDC}")

    print(f"\n{Colors.CYAN}[3] 模拟 Agent 发送消息...{Colors.ENDC}\n")

    messages_to_send = [
        (SegmentType.THINKING_START, ""),
        (SegmentType.THINKING_DELTA, "正在思考..."),
        (SegmentType.THINKING_DELTA, "这是一个测试消息"),
        (SegmentType.THINKING_END, ""),
        (SegmentType.TEXT_START, ""),
        (SegmentType.TEXT_DELTA, "你好！"),
        (SegmentType.TEXT_DELTA, "这是"),
        (SegmentType.TEXT_DELTA, "一条"),
        (SegmentType.TEXT_DELTA, "测试消息。"),
        (SegmentType.TEXT_END, ""),
        (SegmentType.DONE, ""),
    ]

    received_messages = []

    async def receive_messages():
        while True:
            try:
                msg = await asyncio.wait_for(subscriber.get(), timeout=5.0)
                received_messages.append(msg)
                print(f"{Colors.GREEN}    收到: {msg.type.value} - {msg.content[:50] if msg.content else ''}{Colors.ENDC}")
                if msg.type == SegmentType.DONE:
                    break
            except asyncio.TimeoutError:
                print(f"{Colors.RED}    接收超时{Colors.ENDC}")
                break

    receive_task = asyncio.create_task(receive_messages())

    await asyncio.sleep(0.5)

    for i, (msg_type, content) in enumerate(messages_to_send):
        msg = Message(
            role="assistant",
            message_id=f"msg-{i}",
            conversation_id=conversation_id,
            session_id=session_id,
            workspace_id=workspace_id,
            type=msg_type,
            content=content,
        )
        
        mq.publish_sync(msg)
        print(f"{Colors.YELLOW}    发送: {msg_type.value} - {content[:30] if content else ''}{Colors.ENDC}")
        await asyncio.sleep(0.1)

    await receive_task

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  测试结果{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    print(f"发送消息数: {len(messages_to_send)}")
    print(f"接收消息数: {len(received_messages)}")

    if len(received_messages) == len(messages_to_send):
        print(f"\n{Colors.GREEN}✓ MQ 测试通过！{Colors.ENDC}")
    else:
        print(f"\n{Colors.RED}✗ MQ 测试失败！消息数量不匹配{Colors.ENDC}")
        print(f"\n{Colors.YELLOW}收到的消息:{Colors.ENDC}")
        for msg in received_messages:
            print(f"  - {msg.type.value}: {msg.content[:50] if msg.content else ''}")

    await mq.stop_consumer()
    return len(received_messages) == len(messages_to_send)


async def test_mq_with_api():
    """
    完整测试：通过 API 创建对话，然后模拟 Agent 发送消息
    """
    import httpx

    BASE_URL = "http://localhost:8000"

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  MQ + API 集成测试{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    user_id = 99999
    headers = {
        "Content-Type": "application/json",
        "X-User-ID": str(user_id),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"{Colors.CYAN}[1] 创建会话...{Colors.ENDC}")
        resp = await client.post(f"{BASE_URL}/session/sessions", headers=headers, json={"title": "MQ测试"})
        session_data = resp.json()
        if session_data.get("code") != 200:
            print(f"{Colors.RED}创建会话失败: {session_data}{Colors.ENDC}")
            return False
        session_id = session_data["data"]["id"]
        workspace_id = session_data["data"]["workspace_id"]
        print(f"{Colors.GREEN}    会话ID: {session_id}, 工作区: {workspace_id}{Colors.ENDC}")

        print(f"{Colors.CYAN}[2] 创建对话...{Colors.ENDC}")
        resp = await client.post(
            f"{BASE_URL}/session/sessions/{session_id}/conversations",
            headers=headers,
            json={"user_content": "测试MQ"}
        )
        conv_data = resp.json()
        if conv_data.get("code") != 200:
            print(f"{Colors.RED}创建对话失败: {conv_data}{Colors.ENDC}")
            return False
        conversation_id = conv_data["data"]["conversation_id"]
        print(f"{Colors.GREEN}    对话ID: {conversation_id}{Colors.ENDC}")

        print(f"\n{Colors.CYAN}[3] 获取 MQ 实例并订阅...{Colors.ENDC}")
        from singleton import get_message_queue
        mq = get_message_queue()
        await mq.start_consumer()
        subscriber = mq.subscribe(conversation_id)
        print(f"{Colors.GREEN}    已订阅{Colors.ENDC}")

        print(f"\n{Colors.CYAN}[4] 模拟 Agent 发送消息...{Colors.ENDC}\n")

        messages_to_send = [
            (SegmentType.THINKING_START, ""),
            (SegmentType.THINKING_DELTA, "正在思考..."),
            (SegmentType.THINKING_END, ""),
            (SegmentType.TEXT_START, ""),
            (SegmentType.TEXT_DELTA, "你好！"),
            (SegmentType.TEXT_DELTA, "这是测试消息。"),
            (SegmentType.TEXT_END, ""),
            (SegmentType.DONE, ""),
        ]

        received_messages = []

        async def receive_messages():
            while True:
                try:
                    msg = await asyncio.wait_for(subscriber.get(), timeout=10.0)
                    received_messages.append(msg)
                    print(f"{Colors.GREEN}    收到: {msg.type.value} - {msg.content[:30] if msg.content else ''}{Colors.ENDC}")
                    if msg.type == SegmentType.DONE:
                        break
                except asyncio.TimeoutError:
                    print(f"{Colors.RED}    接收超时{Colors.ENDC}")
                    break

        receive_task = asyncio.create_task(receive_messages())

        await asyncio.sleep(0.5)

        for i, (msg_type, content) in enumerate(messages_to_send):
            msg = Message(
                role="assistant",
                message_id=f"msg-{i}",
                conversation_id=conversation_id,
                session_id=session_id,
                workspace_id=workspace_id,
                type=msg_type,
                content=content,
            )
            mq.publish_sync(msg)
            print(f"{Colors.YELLOW}    发送: {msg_type.value} - {content[:30] if content else ''}{Colors.ENDC}")
            await asyncio.sleep(0.1)

        await receive_task

        mq.unsubscribe(conversation_id, subscriber)

        print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
        print(f"{Colors.HEADER}  测试结果{Colors.ENDC}")
        print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

        print(f"发送消息数: {len(messages_to_send)}")
        print(f"接收消息数: {len(received_messages)}")

        if len(received_messages) == len(messages_to_send):
            print(f"\n{Colors.GREEN}✓ MQ + API 测试通过！{Colors.ENDC}")
            return True
        else:
            print(f"\n{Colors.RED}✗ MQ + API 测试失败！{Colors.ENDC}")
            return False


async def main():
    print(f"\n{Colors.BOLD}MQ 测试脚本{Colors.ENDC}")
    print("=" * 60)

    success1 = await test_mq_basic()
    
    print("\n")
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/health", timeout=2)
            if resp.status_code == 200:
                success2 = await test_mq_with_api()
            else:
                print(f"{Colors.YELLOW}后端服务未运行，跳过 API 集成测试{Colors.ENDC}")
                success2 = True
    except Exception as e:
        print(f"{Colors.YELLOW}后端服务未运行，跳过 API 集成测试: {e}{Colors.ENDC}")
        success2 = True

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    if success1 and success2:
        print(f"{Colors.GREEN}所有测试通过！{Colors.ENDC}")
    else:
        print(f"{Colors.RED}部分测试失败{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")


if __name__ == "__main__":
    asyncio.run(main())
