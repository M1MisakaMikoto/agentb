#!/usr/bin/env python3
import asyncio
import httpx
import json

async def test():
    client = httpx.AsyncClient(base_url='http://localhost:8000', timeout=120.0)
    
    print("Creating session...")
    resp = await client.post('/session/sessions', json={'title': 'test'}, headers={'X-User-ID': '1'})
    data = resp.json()
    session_id = data['data']['id']
    workspace_id = data['data']['workspace_id']
    print(f'Session: {session_id}, Workspace: {workspace_id}')
    
    print("Uploading file...")
    with open(r'e:\PythonProject\agentb\.dev\table\城市桥梁养护技术规程（标准文本）.pdf', 'rb') as f:
        resp = await client.post(
            f'/workspaces/{workspace_id}/files',
            files=[('files', ('城市桥梁养护技术规程（标准文本）.pdf', f, 'application/pdf'))],
            headers={'X-User-ID': '1'}
        )
    print(f'Upload: {resp.status_code}')
    
    print("Creating conversation...")
    prompt = '请在当前工作区内完成一个只读任务。工作区里已经有一个文件，文件名是"城市桥梁养护技术规程（标准文本）.pdf"。请先查看工作区里有哪些文件并确认该文件位置；再使用 read_document 工具读取这个文件；然后总结文件的主要内容。不要修改任何文件，不要创建新文件，最后输出简短结论。'
    resp = await client.post(
        f'/session/sessions/{session_id}/conversations',
        json={'user_content': prompt},
        headers={'X-User-ID': '1'}
    )
    data = resp.json()
    conv_id = data['data']['conversation_id']
    print(f'Conversation: {conv_id}')
    
    print("Streaming...")
    count = 0
    tool_calls = []
    async with client.stream('GET', f'/session/conversations/{conv_id}/stream?last_seq=0', headers={'X-User-ID': '1'}) as resp:
        async for line in resp.aiter_lines():
            print(f'RAW: {line[:200]}')
            if line.startswith('data: '):
                try:
                    d = json.loads(line[6:])
                    etype = d.get('type', 'unknown')
                    content = d.get('content', '') or d.get('metadata', {})
                    print(f'[{etype}] {str(content)[:150]}')
                    if etype == 'tool_call':
                        tool_calls.append(d.get('metadata', {}))
                    count += 1
                except Exception as e:
                    print(f'[parse error] {e}')
                    
    print(f"Total events: {count}")
    print(f"Tool calls: {tool_calls}")
    
    print("Checking final state...")
    resp = await client.get(f'/session/conversations/{conv_id}', headers={'X-User-ID': '1'})
    data = resp.json()
    print(f'Final state: {json.dumps(data, ensure_ascii=False, indent=2)}')
    
    # Get full conversation
    print("Full conversation content:")
    resp = await client.get(f'/session/conversations/{conv_id}', headers={'X-User-ID': '1'})
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    
asyncio.run(test())
