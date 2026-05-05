import sys, os, traceback, json, asyncio
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend\.test')
os.chdir(r'e:\PythonProject\agentb\WorkBranch\backend\.test')

log_file = r'e:\PythonProject\agentb\WorkBranch\backend\.test\logs\doc_verify_result.txt'

def log(msg):
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')
    print(msg, flush=True)

async def main():
    try:
        log('=== IMPORT START ===')
        from test_cases.base import APIClient, load_config
        log('=== IMPORT OK ===')
        
        config = load_config()
        api = APIClient(config)
        log('=== API OK ===')
        
        session = await api.create_session(title='doc_test3')
        ws_id = session.get('data', {}).get('workspace_id', '')
        log(f'Workspace: {ws_id}')
        
        from pathlib import Path
        doc_file = Path(r'e:\PythonProject\agentb\.dev\table\桥梁检测报告\2020\03 陈家阁大桥.doc')
        upload = await api.upload_workspace_file(ws_id, doc_file)
        log(f'Upload: {upload.get("code")}')
        
        conv = await api.create_conversation(session['data']['id'], '用read_document工具读取03陈家阁大桥.doc，告诉我前200字内容')
        conv_id = conv.get('data', {}).get('conversation_id', '')
        log(f'ConvID: {conv_id}')
        
        for i in range(6):
            await asyncio.sleep(10)
            conv_state = await api.get_conversation(conv_id)
            data = conv_state.get('data', {})
            state = data.get('state', '')
            content = data.get('assistant_content', '')
            log(f'[T+{i*10}s] state={state} content_len={len(content) if content else 0}')
            
            if state in ('completed', 'stopped') or (content and len(content) > 100):
                break
        
        if content:
            events = json.loads(content) if isinstance(content, str) else content
            for ev in events[-15:]:
                etype = ev.get('type', '')
                if etype in ('text_delta', 'chat_delta'):
                    log(f'  [{etype}] {ev.get("content", "")[:200]}')
                elif etype == 'tool_call':
                    meta = ev.get('metadata', {})
                    log(f'  [tool_call] {meta.get("tool_name", "?")}')
                elif etype == 'error':
                    log(f'  [ERROR] {ev.get("content", "")[:200]}')
        
        log('=== DONE ===')
    except Exception as e:
        log(f'=== EXCEPTION: {type(e).__name__}: {e} ===')
        traceback.print_exc()
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(traceback.format_exc())

try:
    asyncio.run(main())
except Exception as e:
    log(f'TOP EXCEPTION: {e}')
