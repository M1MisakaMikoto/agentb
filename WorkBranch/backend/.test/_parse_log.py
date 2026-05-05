with open(r'e:\PythonProject\agentb\WorkBranch\backend\.test\logs\doc_tool_debug.log', 'rb') as f:
    data = f.read()

if data[:2] == b'\xff\xfe':
    text = data.decode('utf-16-le')
else:
    text = data.decode('utf-8', errors='replace')

with open(r'e:\PythonProject\agentb\WorkBranch\backend\.test\logs\debug_parsed.log', 'w', encoding='utf-8') as out:
    for line in text.split('\n'):
        if line.strip():
            out.write(line.strip() + '\n')

print(f'Done - wrote {text.count(chr(10))} lines')
