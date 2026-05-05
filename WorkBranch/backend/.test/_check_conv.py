import sys, json, httpx
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend\.test')
from test_cases.base import load_config

config = load_config()
base = config['api']['base_url']
user_id = config.get('user_id', 1)
headers = {'X-User-ID': str(user_id)}

conv_id = '404ff92f-b169-41dd-8c70-8c12edbadb1b'
r = httpx.get(f'{base}/conversations/{conv_id}', headers=headers, timeout=10)
d = r.json()
data = d.get('data', {})

result = {
    'state': data.get('state'),
    'error': data.get('error'),
    'assistant_content_len': len(data.get('assistant_content', '') or ''),
    'has_stream': bool(data.get('stream_id'))
}
print(json.dumps(result, ensure_ascii=False, indent=2))

ac = data.get('assistant_content', '')
if ac:
    events = json.loads(ac) if isinstance(ac, str) else ac
    for ev in events[-10:]:
        print(json.dumps(ev, ensure_ascii=False)[:300])
