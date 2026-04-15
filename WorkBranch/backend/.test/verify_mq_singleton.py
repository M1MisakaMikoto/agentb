#!/usr/bin/env python3
"""
验证 MQ 单例是否一致
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

async def main():
    from singleton import get_message_queue, get_agent_service
    
    mq1 = get_message_queue()
    print(f"get_message_queue() id: {id(mq1)}", flush=True)
    
    agent = get_agent_service()
    print(f"agent._message_queue id: {id(agent._message_queue)}", flush=True)
    
    mq2 = agent._get_message_queue()
    print(f"agent._get_message_queue() id: {id(mq2)}", flush=True)
    
    result = mq1 is mq2
    print(f"\nMQ 实例是否一致: {result}", flush=True)
    
    if not result:
        print("\n问题: Agent 使用了不同的 MQ 实例!", flush=True)
        print("这会导致消息无法正确传递到前端", flush=True)
    else:
        print("\nMQ 实例一致，问题可能在其他地方", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
