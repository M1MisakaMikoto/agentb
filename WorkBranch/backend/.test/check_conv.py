import urllib.request
import json

conv_id = '7a44fdfb-ff1f-4bbc-a545-a64f9cbaa224'

req = urllib.request.Request(f'http://localhost:8000/session/conversations/{conv_id}')
req.add_header('X-User-ID', '1')
with urllib.request.urlopen(req) as r:
    data = json.loads(r.read().decode())
    d = data.get('data', {})
    print(f"state: {d.get('state')}")
