import sys, os
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend')
from service.agent_service.tools.document_tools import _convert_doc_to_docx

doc = r'e:\PythonProject\agentb\.dev\table\桥梁检测报告\2020\03 陈家阁大桥.doc'
lines = []
lines.append(f'File exists: {os.path.exists(doc)}')
lines.append(f'File size: {os.path.getsize(doc) if os.path.exists(doc) else "N/A"}')

try:
    result = _convert_doc_to_docx(doc)
    lines.append(f'Result: {result}')
except Exception as e:
    lines.append(f'Exception: {type(e).__name__}: {e}')

open(r'e:\PythonProject\agentb\WorkBranch\backend\.test\logs\convert_test.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print('DONE')
