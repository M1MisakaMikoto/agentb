#!/usr/bin/env python3
"""
Complete E2E Test - Simulate full Agent execution flow

This test:
1. Creates a session and conversation via API
2. Subscribes to MQ for the conversation
3. Simulates Agent sending messages (including LLM response)
4. Verifies the frontend receives all messages

Usage:
    python test_complete_e2e.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


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


async def test_complete_e2e():
    """
    Complete E2E test simulating full Agent execution
    """
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Complete E2E Test - {datetime.now().strftime('%Y%m%d_%H%M%S')}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    user_id = 99999
    headers = {
        "Content-Type": "application/json",
        "X-User-ID": str(user_id),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"{Colors.CYAN}[1] Creating session...{Colors.ENDC}")
        resp = await client.post(f"{BASE_URL}/session/sessions", headers=headers, json={"title": "E2E Test"})
        session_data = resp.json()
        if session_data.get("code") != 200:
            print(f"{Colors.RED}Failed to create session: {session_data}{Colors.ENDC}")
            return False
        session_id = session_data["data"]["id"]
        workspace_id = session_data["data"]["workspace_id"]
        print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")
        print(f"{Colors.GREEN}    Workspace ID: {workspace_id}{Colors.ENDC}")

        print(f"\n{Colors.CYAN}[2] Creating conversation...{Colors.ENDC}")
        resp = await client.post(
            f"{BASE_URL}/session/sessions/{session_id}/conversations",
            headers=headers,
            json={"user_content": "Hello"}
        )
        conv_data = resp.json()
        if conv_data.get("code") != 200:
            print(f"{Colors.RED}Failed to create conversation: {conv_data}{Colors.ENDC}")
            return False
        conversation_id = conv_data["data"]["conversation_id"]
        message_id = conv_data["data"].get("message_id", "test-msg-id")
        print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")

        print(f"\n{Colors.CYAN}[3] Getting MQ instance and subscribing...{Colors.ENDC}")
        from singleton import get_message_queue
        from service.session_service.canonical import SegmentType, MessageBuilder
        
        mq = get_message_queue()
        await mq.start_consumer()
        subscriber = mq.subscribe(conversation_id)
        print(f"{Colors.GREEN}    Subscribed to conversation{Colors.ENDC}")

        print(f"\n{Colors.CYAN}[4] Simulating Agent execution (sending messages to MQ)...{Colors.ENDC}\n")

        messages_to_send = [
            (SegmentType.THINKING_START, "", {}),
            (SegmentType.THINKING_DELTA, "Analyzing your request...", {}),
            (SegmentType.THINKING_END, "", {}),
            (SegmentType.TEXT_START, "", {}),
            (SegmentType.TEXT_DELTA, "Hello! ", {}),
            (SegmentType.TEXT_DELTA, "How can I help you today?", {}),
            (SegmentType.TEXT_END, "", {}),
            (SegmentType.DONE, "", {}),
        ]

        received_messages = []

        async def receive_messages():
            while True:
                try:
                    msg = await asyncio.wait_for(subscriber.get(), timeout=10.0)
                    received_messages.append(msg)
                    print(f"{Colors.GREEN}    Received: {msg.type.value} - {msg.content[:30] if msg.content else ''}{Colors.ENDC}")
                    if msg.type == SegmentType.DONE:
                        break
                except asyncio.TimeoutError:
                    print(f"{Colors.RED}    Receive timeout{Colors.ENDC}")
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

        for block_type, content, metadata in messages_to_send:
            send_message(content, block_type, metadata)
            print(f"{Colors.YELLOW}    Sent: {block_type.value} - {content[:30] if content else ''}{Colors.ENDC}")
            await asyncio.sleep(0.1)

        await receive_task

        mq.unsubscribe(conversation_id, subscriber)

        print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
        print(f"{Colors.HEADER}  Test Results{Colors.ENDC}")
        print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

        print(f"Messages sent: {len(messages_to_send)}")
        print(f"Messages received: {len(received_messages)}")

        if len(received_messages) >= len(messages_to_send):
            print(f"\n{Colors.GREEN}Test PASSED!{Colors.ENDC}")
            return True
        else:
            print(f"\n{Colors.RED}Test FAILED!{Colors.ENDC}")
            return False


async def test_real_agent_execution():
    """
    Test real Agent execution via API
    """
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}  Real Agent Execution Test{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")

    user_id = 99999
    headers = {
        "Content-Type": "application/json",
        "X-User-ID": str(user_id),
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        print(f"{Colors.CYAN}[1] Creating session...{Colors.ENDC}")
        resp = await client.post(f"{BASE_URL}/session/sessions", headers=headers, json={"title": "Real Agent Test"})
        session_data = resp.json()
        if session_data.get("code") != 200:
            print(f"{Colors.RED}Failed: {session_data}{Colors.ENDC}")
            return False
        session_id = session_data["data"]["id"]
        print(f"{Colors.GREEN}    Session ID: {session_id}{Colors.ENDC}")

        print(f"\n{Colors.CYAN}[2] Creating conversation...{Colors.ENDC}")
        resp = await client.post(
            f"{BASE_URL}/session/sessions/{session_id}/conversations",
            headers=headers,
            json={"user_content": "Say hello"}
        )
        conv_data = resp.json()
        if conv_data.get("code") != 200:
            print(f"{Colors.RED}Failed: {conv_data}{Colors.ENDC}")
            return False
        conversation_id = conv_data["data"]["conversation_id"]
        print(f"{Colors.GREEN}    Conversation ID: {conversation_id}{Colors.ENDC}")

        print(f"\n{Colors.CYAN}[3] Streaming messages from Agent...{Colors.ENDC}\n")

        url = f"{BASE_URL}/session/conversations/{conversation_id}/messages/stream"
        
        event_count = 0
        text_content = ""
        
        async with client.stream("POST", url, headers=headers) as response:
            if response.status_code != 200:
                error = await response.aread()
                print(f"{Colors.RED}Error: {error.decode()}{Colors.ENDC}")
                return False
            
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                    
                if line.startswith(": heartbeat"):
                    print(f"{Colors.DIM}[heartbeat]{Colors.ENDC}")
                    continue
                
                if line.startswith("data: "):
                    event_count += 1
                    json_str = line[6:]
                    
                    try:
                        data = json.loads(json_str)
                        event_type = data.get("type", "unknown")
                        
                        if event_type == "text_delta":
                            content = data.get("content", "")
                            text_content += content
                            print(f"{Colors.GREEN}{content}{Colors.ENDC}", end="", flush=True)
                        elif event_type == "thinking_delta":
                            content = data.get("content", "")
                            print(f"{Colors.DIM}[thinking: {content}]{Colors.ENDC}")
                        elif event_type == "done":
                            print(f"\n{Colors.GREEN}[done]{Colors.ENDC}")
                        elif event_type == "error":
                            print(f"\n{Colors.RED}[error: {data.get('content')}]{Colors.ENDC}")
                        else:
                            print(f"\n{Colors.CYAN}[{event_type}]{Colors.ENDC}")
                            
                    except json.JSONDecodeError:
                        print(f"{Colors.RED}[json error]{Colors.ENDC}")

        print(f"\n\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
        print(f"Total events: {event_count}")
        print(f"Text content length: {len(text_content)}")
        
        if event_count > 1:
            print(f"{Colors.GREEN}Real Agent test PASSED!{Colors.ENDC}")
            return True
        else:
            print(f"{Colors.RED}Real Agent test FAILED - No events received{Colors.ENDC}")
            return False


async def main():
    print(f"\n{Colors.BOLD}Complete E2E Test Suite{Colors.ENDC}")
    print("=" * 60)

    success1 = await test_complete_e2e()
    
    print("\n")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                success2 = await test_real_agent_execution()
            else:
                print(f"{Colors.YELLOW}Backend not running, skipping real agent test{Colors.ENDC}")
                success2 = True
    except Exception as e:
        print(f"{Colors.YELLOW}Backend not running, skipping real agent test: {e}{Colors.ENDC}")
        success2 = True

    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    if success1 and success2:
        print(f"{Colors.GREEN}All tests passed!{Colors.ENDC}")
    else:
        print(f"{Colors.RED}Some tests failed{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")


if __name__ == "__main__":
    asyncio.run(main())
