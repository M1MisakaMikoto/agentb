import sys
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend')
from service.agent_service.tools.document_tools import _convert_doc_to_docx

files = [
    (r'E:\PythonProject\agentb\.dev\table\桥梁检测报告\2020\03 陈家阁大桥.doc', '2020'),
    (r'E:\PythonProject\agentb\.dev\table\桥梁检测报告\2022\09 陈家阁立交.doc', '2022'),
]

with open(r'e:\PythonProject\agentb\WorkBranch\backend\.test\logs\doc_convert_test.log', 'w', encoding='utf-8') as out:
    for fpath, label in files:
        out.write(f'=== {label}: {fpath.split(chr(92))[-1]} ===\n')
        result = _convert_doc_to_docx(fpath)
        out.write(f'Result: {result}\n')
        if result:
            import os
            size = os.path.getsize(result) if os.path.exists(result) else 0
            out.write(f'Size: {size} bytes\n')
        out.write('\n')

print('Done, check doc_convert_test.log')
