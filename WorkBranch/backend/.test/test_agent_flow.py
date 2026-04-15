#!/usr/bin/env python3
"""
测试 Agent 执行流程 - 验证消息是否正确发送
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from service.session_service.mq import MessageQueue
from service.session_service.canonical import SegmentType, Message, MessageBuilder
from service.settings_service.settings_service import SettingsService
from singleton import get_message_queue, get_agent_service


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


async def test_agent_send_message():
    """
    测试 Agent 的 send_message 函数是否正确发送消息到 MQ
    """
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Agent send_message 测试{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    from service.agent_service.agent_service import AgentService, ConversationStatus

    settings = SettingsService()
    mq = MessageQueue(settings)
    
    conversation_id = "test-conv-agent-001"
    workspace_id = "test-workspace-001"
    session_id = "test-session-001"
    message_id = "test-msg-001"

    print(f"{Colors.CYAN}[1] 启动 MQ 消费者...{Colors.ENDC}")
    await mq.start_consumer()
    print(f"{Colors.GREEN}    MQ 消费者已启动{Colors.ENDC}")

    print(f"{Colors.CYAN}[2] 订阅消息...{Colors.ENDC}")
    subscriber = mq.subscribe(conversation_id)
    print(f"{Colors.GREEN}    已订阅 conversation_id: {conversation_id}{Colors.ENDC}")

    print(f"\n{Colors.CYAN}[3] 模拟 Agent 的 send_message 函数...{Colors.ENDC}\n")

    received_messages = []

    async def receive_messages():
        while True:
            try:
                msg = await asyncio.wait_for(subscriber.get(), timeout=5.0)
                received_messages.append(msg)
                print(f"{Colors.GREEN}    收到: {msg.type.value} - {msg.content[:30] if msg.content else ''}{Colors.ENDC}")
                if msg.type == SegmentType.DONE:
                    break
            except asyncio.TimeoutError:
                print(f"{Colors.RED}    接收超时{Colors.ENDC}")
                break

    receive_task = asyncio.create_task(receive_messages())

    await asyncio.sleep(0.5)

    text_started = False

    def send_message(content: str = "", block_type: SegmentType = SegmentType.TEXT_DELTA, metadata: dict = None):
        nonlocal text_started
        merged_metadata = {"message_id": message_id}
        if metadata:
            merged_metadata.update(metadata)
        
        if block_type == SegmentType.TEXT_DELTA:
            if not text_started:
                msg = MessageBuilder.text_start(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    metadata=merged_metadata,
                )
                mq.publish_sync(msg)
                text_started = True
            
            msg = MessageBuilder.text_delta(
                message_id=message_id,
                conversation_id=conversation_id,
                session_id=session_id,
                workspace_id=workspace_id,
                content=content,
            )
        else:
            msg = MessageBuilder.build(
                role="assistant",
                message_id=message_id,
                conversation_id=conversation_id,
                session_id=session_id,
                workspace_id=workspace_id,
                msg_type=block_type,
                content=content,
                metadata=merged_metadata,
            )
        mq.publish_sync(msg)

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

    for block_type, content in messages_to_send:
        send_message(content, block_type)
        print(f"{Colors.YELLOW}    发送: {block_type.value} - {content[:30] if content else ''}{Colors.ENDC}")
        await asyncio.sleep(0.1)

    await receive_task

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  测试结果{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    print(f"发送消息数: {len(messages_to_send)}")
    print(f"接收消息数: {len(received_messages)}")

    if len(received_messages) == len(messages_to_send):
        print(f"\n{Colors.GREEN}✓ Agent send_message 测试通过！{Colors.ENDC}")
        result = True
    else:
        print(f"\n{Colors.RED}✗ Agent send_message 测试失败！{Colors.ENDC}")
        result = False

    await mq.stop_consumer()
    return result


async def test_llm_service():
    """
    测试 LLM 服务是否正常工作
    """
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  LLM 服务测试{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    from singleton import get_llm_service

    llm = get_llm_service()
    
    print(f"{Colors.CYAN}[1] 测试 LLM 服务...{Colors.ENDC}")
    
    messages = [{"role": "user", "content": "你好，请简短回复"}]
    
    try:
        print(f"{Colors.YELLOW}    发送消息: 你好，请简短回复{Colors.ENDC}")
        
        response = ""
        for chunk in llm.chat_stream(messages, "你是一个友好的助手，请简短回复。"):
            response += chunk
            print(f"{Colors.GREEN}    收到: {chunk}{Colors.ENDC}", end="", flush=True)
        
        print(f"\n\n{Colors.GREEN}    完整响应: {response[:200]}{Colors.ENDC}")
        
        if response:
            print(f"\n{Colors.GREEN}✓ LLM 服务测试通过！{Colors.ENDC}")
            return True
        else:
            print(f"\n{Colors.RED}✗ LLM 服务返回空响应{Colors.ENDC}")
            return False
            
    except Exception as e:
        print(f"\n{Colors.RED}✗ LLM 服务测试失败: {e}{Colors.ENDC}")
        return False


async def main():
    print(f"\n{Colors.BOLD}Agent 执行流程测试{Colors.ENDC}")
    print("=" * 60)

    success1 = await test_agent_send_message()
    
    print("\n")
    
    success2 = await test_llm_service()

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    if success1 and success2:
        print(f"{Colors.GREEN}所有测试通过！{Colors.ENDC}")
    else:
        print(f"{Colors.RED}部分测试失败{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")


if __name__ == "__main__":
    asyncio.run(main())
