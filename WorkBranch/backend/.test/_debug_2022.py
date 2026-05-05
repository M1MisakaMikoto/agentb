import sys
import os
import traceback

sys.path.insert(0, r'e:\PythonProject\agentb\WorkBranch\backend')

file_2022 = r'E:\PythonProject\agentb\.dev\table\桥梁检测报告\2022\09 陈家阁立交.doc'
log_file = r'e:\PythonProject\agentb\WorkBranch\backend\.test\logs\debug_2022_result.txt'

try:
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"File: {os.path.basename(file_2022)}\n")
        f.write(f"Size: {os.path.getsize(file_2022):,} bytes\n")
        f.write(f"Exists: {os.path.exists(file_2022)}\n\n")
        
        f.write("Starting conversion...\n")
        f.flush()
        
        from service.agent_service.tools.document_tools import _convert_doc_to_docx
        result = _convert_doc_to_docx(file_2022)
        
        f.write(f"RESULT: {result}\n")
        if result and os.path.exists(result):
            f.write(f"Output size: {os.path.getsize(result):,} bytes\n")
        else:
            f.write("CONVERSION FAILED!\n")
except Exception as e:
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(f"ERROR: {type(e).__name__}: {e}\n")
        traceback.print_exc(file=f)

print("Done")
