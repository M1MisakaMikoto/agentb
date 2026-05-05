import sys, os
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend')

import importlib
import service.agent_service.tools.document_tools as dt

print('Module file:', dt.__file__, flush=True)
print('_convert_doc_to_docx source:', flush=True)

import inspect
src = inspect.getsource(dt._convert_doc_to_docx)
for i, line in enumerate(src.split('\n')):
    if 'win32com' in line.lower() or 'word' in line.lower() or 'com' in line.lower():
        print(f'  L{i}: {line}', flush=True)
