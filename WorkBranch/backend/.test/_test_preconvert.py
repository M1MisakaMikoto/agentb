import sys
import os
sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend')

from service.agent_service.tools.document_tools import _convert_doc_to_docx

files = {
    "2018": r'E:\PythonProject\agentb\.dev\table\桥梁检测报告\2018\12陈家阁大桥定期检测2018.10.docx',
    "2020": r'E:\PythonProject\agentb\.dev\table\桥梁检测报告\2020\03 陈家阁大桥.doc',
    "2022": r'E:\PythonProject\agentb\.dev\table\桥梁检测报告\2022\09 陈家阁立交.doc',
}

log = []
for year, fpath in files.items():
    log.append(f"\n=== {year}: {os.path.basename(fpath)} ===")
    if not os.path.exists(fpath):
        log.append("FILE NOT FOUND!")
        continue
    
    if fpath.endswith('.doc'):
        log.append("Converting .doc to .docx...")
        result = _convert_doc_to_docx(fpath)
        if result and os.path.exists(result):
            size = os.path.getsize(result)
            log.append(f"SUCCESS: {os.path.basename(result)} ({size:,} bytes)")
        else:
            log.append("FAILED!")
    else:
        size = os.path.getsize(fpath)
        log.append(f"Already docx ({size:,} bytes)")

with open(r'e:\PythonProject\agentb\WorkBranch\backend\.test\logs\pre_convert_test.log', 'w', encoding='utf-8') as f:
    f.write('\n'.join(log))

print('\n'.join(log))
print("\nDone!")
