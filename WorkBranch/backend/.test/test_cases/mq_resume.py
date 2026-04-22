#!/usr/bin/env python3
"""
MQ Resume Test

测试消息队列断点续传功能
"""

import asyncio
import time
from dataclasses import dataclass
from typing import List, Optional

from .base import (
    APIClient,
    TestResult,
    Colors,
    print_test_header,
    print_step,
    print_success,
    print_error,
    print_dim,
    collect_stream_output,
    wait_for_conversation_state,
    extract_response_text,
)


@dataclass
class StreamMessage:
    seq: int
    event_type: str
    content: str
    timestamp: float


@dataclass
class StreamResult:
    messages: List[StreamMessage]
    last_seq: int
    done: bool


async def collect_stream_with_seq(
    api: APIClient,
    conversation_id: str,
    last_seq: int = 0,
    max_messages: Optional[int] = None,
    verbose: bool = True,
) -> StreamResult:
    messages = []
    current_seq = last_seq
    done = False
    count = 0
    
    async for item in api.stream_message(conversation_id, last_seq=last_seq):
        raw_line = item.get("raw_line", "")
        if not raw_line.strip():
            continue
        
        if raw_line.startswith(": heartbeat"):
            continue
        
        if not raw_line.startswith("data: "):
            continue
        
        try:
            import json
            data = json.loads(raw_line[6:])
        except json.JSONDecodeError:
            continue
        
        event_type = data.get("type", "unknown")
        seq = data.get("seq", current_seq + 1)
        current_seq = seq
        
        content = ""
        if event_type in ("text_delta", "chat_delta", "thinking_delta"):
            content = data.get("content", "")
        elif event_type == "tool_call":
            metadata = data.get("metadata", {})
            content = metadata.get("tool_name", "unknown")
        elif event_type == "done":
            done = True
        
        messages.append(StreamMessage(
            seq=seq,
            event_type=event_type,
            content=content,
            timestamp=time.time(),
        ))
        
        if verbose and content:
            print(f"  [seq={seq}] [{event_type}] {content[:50]}...")
        
        count += 1
        if max_messages and count >= max_messages:
            break
    
    return StreamResult(messages=messages, last_seq=current_seq, done=done)


async def run_mq_resume_test(api: APIClient, scenario_config: dict, verbose: bool = True) -> TestResult:
    result = TestResult("mq_resume", scenario_config)
    
    print_test_header(scenario_config.get("description", "MQ Resume Test"))
    
    test_prompts = scenario_config.get("test_prompts", {})
    
    print_step(1, "Creating session...", Colors.CYAN)
    session_result = await api.create_session(title="MQ Resume Test")
    if session_result.get("code") != 0:
        print_error(f"Failed to create session: {session_result.get('message')}")
        result.errors.append(f"create_session: {session_result.get('message')}")
        return result
    
    session_id = session_result.get("data", {}).get("id")
    result.session_id = session_id
    print_success(f"Session created: {session_id}")
    
    print_step(2, "Creating conversation...", Colors.CYAN)
    normal_prompt = test_prompts.get("normal", "请用至少100字介绍一下Python的异步编程。")
    conv_result = await api.create_conversation(session_id, normal_prompt)
    if conv_result.get("code") != 0:
        print_error(f"Failed to create conversation: {conv_result.get('message')}")
        result.errors.append(f"create_conversation: {conv_result.get('message')}")
        return result
    
    conversation_id = conv_result.get("data", {}).get("conversation_id")
    result.conversation_id = conversation_id
    print_success(f"Conversation created: {conversation_id}")
    
    print_step(3, "Receiving first part of stream (max 5 messages)...", Colors.CYAN)
    first_part = await collect_stream_with_seq(api, conversation_id, max_messages=5, verbose=verbose)
    first_last_seq = first_part.last_seq
    first_messages = first_part.messages.copy()
    print_success(f"Received {len(first_messages)} messages, last_seq={first_last_seq}")
    
    print_step(4, f"Simulating disconnect at seq={first_last_seq}", Colors.YELLOW)
    await asyncio.sleep(0.5)
    
    print_step(5, f"Resuming from seq={first_last_seq}...", Colors.CYAN)
    resumed_part = await collect_stream_with_seq(api, conversation_id, last_seq=first_last_seq, verbose=verbose)
    print_success(f"Resumed and received {len(resumed_part.messages)} messages")
    
    print_step(6, "Waiting for conversation to complete...", Colors.CYAN)
    await wait_for_conversation_state(api, conversation_id, "completed", timeout=60.0)
    
    print_step(7, "Validating resume functionality...", Colors.CYAN)
    
    all_messages = first_messages + resumed_part.messages
    unique_seqs = set(msg.seq for msg in all_messages)
    
    if len(unique_seqs) == len(all_messages):
        print_success("No duplicate messages - resume works correctly")
    else:
        print_error(f"Duplicate messages detected: {len(all_messages)} total, {len(unique_seqs)} unique")
        result.errors.append("Duplicate messages in resume")
    
    if resumed_part.done:
        print_success("Stream completed successfully after resume")
    else:
        print_dim("Stream may not have completed (check conversation state)")
    
    print_step(8, "Testing reconnection after completion...", Colors.CYAN)
    await asyncio.sleep(0.5)
    
    reconnect_part = await collect_stream_with_seq(api, conversation_id, last_seq=0, verbose=False)
    if len(reconnect_part.messages) > 0:
        print_success(f"Reconnection successful: {len(reconnect_part.messages)} messages received")
    else:
        print_dim("No messages on reconnection (expected for completed conversation)")
    
    result.response_text = f"First part: {len(first_messages)} messages\nResumed: {len(resumed_part.messages)} messages"
    result.event_count = len(all_messages)
    
    print(f"\n{Colors.GREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.GREEN}  MQ Resume Test Completed{Colors.ENDC}")
    print(f"{Colors.GREEN}{'='*60}{Colors.ENDC}\n")
    
    return result
