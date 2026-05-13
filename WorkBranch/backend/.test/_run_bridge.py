import sys, os, re
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend\.test')
os.chdir(r'e:\PythonProject\agentb\WorkBranch\backend\.test')

import asyncio
from test_cases.bridge_predict import run_bridge_predict_test
from test_cases.base import APIClient, load_config


def merge_delta_logs(log_file):
    if not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
        return
    
    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    blocks = []
    current_block = []
    for line in lines:
        if '=== 🔍 DIAGNOSTIC:' in line and current_block:
            blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)
    if current_block:
        blocks.append(current_block)
    
    merged = []
    buffer = []
    buffer_type = None
    
    for block in blocks:
        is_diagnostic = any('MQ PUBLISH_SYNC' in b for b in block)
        
        if not is_diagnostic:
            if buffer:
                merged.append(_format_merged(buffer_type, buffer))
                buffer = []
                buffer_type = None
            merged.extend(block)
            continue
        
        ts = ''
        for b in block:
            if '[' in b and ']' in b:
                ts = b.split(']')[0] + ']'
                break
        
        msg_type = None
        content = None
        
        for b in block:
            stripped = b.strip()
            if '- type:' in stripped:
                t = stripped.split('type:')[1].strip()
                if t in ('thinking_delta', 'chat_delta'):
                    msg_type = t
            if '- content preview:' in stripped and msg_type:
                content = stripped.split('content preview:')[1].strip()
        
        if msg_type and content:
            if buffer_type == msg_type and buffer:
                buffer.append((ts, content))
            else:
                if buffer:
                    merged.append(_format_merged(buffer_type, buffer))
                buffer = [(ts, content)]
                buffer_type = msg_type
        else:
            if buffer:
                merged.append(_format_merged(buffer_type, buffer))
                buffer = []
                buffer_type = None
            merged.extend(block)
    
    if buffer:
        merged.append(_format_merged(buffer_type, buffer))
    
    with open(log_file, 'w', encoding='utf-8') as f:
        f.writelines(merged)


def _format_merged(msg_type, entries):
    ts_start = entries[0][0]
    seq_end = len(entries) - 1
    combined_content = ''.join(e[1] for e in entries)
    
    return (
        f'{ts_start} === 🔍 DIAGNOSTIC: MQ PUBLISH_SYNC ===\n'
        f'{ts_start} 📤 Message details:\n'
        f'  - type: {msg_type} (merged {len(entries)} deltas)\n'
        f'  - content length: {len(combined_content)} chars\n'
        f'  - content preview: {combined_content}\n\n'
    )

async def main():
    log_file = r'e:\PythonProject\agentb\WorkBranch\backend\llm_decision_trace.log'
    if os.path.exists(log_file):
        open(log_file, 'w').close()
    
    config = load_config()
    api = APIClient(config)
    scenario_cfg = config.get('scenarios', {}).get('bridge_predict', {})
    scenario_cfg['prediction_timeout'] = 900.0
    
    result = await run_bridge_predict_test(api, scenario_cfg, verbose=True)
    
    print()
    print('='*60)
    print(f'Errors: {result.errors}')
    print(f'Tool calls: {result.tool_calls}')
    print(f'Response length: {len(result.response_text)}')
    print(f'Done: {result.done}')
    if not result.errors:
        print('\n*** TEST PASSED ***')
    else:
        print('\n*** TEST COMPLETED WITH ISSUES ***')
    print('='*60)
    
    merge_delta_logs(log_file)
    print(f'\n✅ 日志已合并: {log_file}')

asyncio.run(main())
