import os
from typing import Optional, List, Dict, Any

from .registry import ToolDefinition, ToolRegistry
from singleton import get_settings_service


def _read_pdf(file_path: str, start_idx: int = 0, max_length: int = 10000, include_metadata: bool = True, use_llm_parsing: bool = True) -> dict:
    """读取PDF文件
    
    Args:
        use_llm_parsing: True 使用 pymupdf4llm (LLM优化格式), False 使用 pypdf (快速纯文本)
    """
    try:
        import pypdf
    except ImportError:
        return {"result": None, "error": "缺少依赖: pip install pypdf"}
    
    try:
        if use_llm_parsing:
            try:
                import pymupdf4llm
            except ImportError:
                return {"result": None, "error": "缺少依赖: pip install pymupdf4llm"}
            
            md_text = pymupdf4llm.to_markdown(file_path)
            full_text = md_text
        else:
            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                text_parts = []
                for page in reader.pages:
                    text_parts.append(page.extract_text() or "")
                full_text = "\n".join(text_parts)
        
        total_length = len(full_text)
        end_idx = min(start_idx + max_length, total_length)
        content = full_text[start_idx:end_idx]
        
        metadata = {}
        if include_metadata:
            try:
                with open(file_path, "rb") as f:
                    reader = pypdf.PdfReader(f)
                    metadata = {
                        "file_type": "pdf",
                        "page_count": len(reader.pages),
                        "author": reader.metadata.author if reader.metadata else None,
                        "title": reader.metadata.title if reader.metadata else None,
                        "creator": reader.metadata.creator if reader.metadata else None,
                        "producer": reader.metadata.producer if reader.metadata else None,
                        "creation_date": str(reader.metadata.creation_date) if reader.metadata and reader.metadata.creation_date else None,
                        "parsing_mode": "llm" if use_llm_parsing else "fast",
                    }
            except Exception:
                metadata = {"file_type": "pdf", "page_count": "unknown", "parsing_mode": "llm" if use_llm_parsing else "fast"}
        
        return {
            "result": {
                "content": content,
                "metadata": metadata,
                "total_length": total_length,
                "read_range": f"{start_idx}-{end_idx}",
                "truncated": end_idx < total_length
            },
            "error": None
        }
    
    except Exception as e:
        return {"result": None, "error": f"PDF读取失败: {str(e)}"}


def _read_docx(file_path: str, start_idx: int = 0, max_length: int = 10000, include_metadata: bool = True) -> dict:
    """读取Word文档"""
    try:
        from docx import Document
    except ImportError:
        return {"result": None, "error": "缺少依赖: pip install python-docx"}
    
    try:
        doc = Document(file_path)
        
        paragraphs = []
        structure = []
        
        for i, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if text:
                paragraphs.append(text)
                structure.append({
                    "type": "paragraph",
                    "index": i,
                    "style": para.style.name if para.style else None,
                    "content": text[:100] + "..." if len(text) > 100 else text
                })
        
        full_text = "\n\n".join(paragraphs)
        total_length = len(full_text)
        end_idx = min(start_idx + max_length, total_length)
        content = full_text[start_idx:end_idx]
        
        metadata = {}
        if include_metadata:
            core_props = doc.core_properties
            metadata = {
                "file_type": "docx",
                "paragraph_count": len(paragraphs),
                "author": core_props.author,
                "title": core_props.title,
                "subject": core_props.subject,
                "keywords": core_props.keywords,
                "created": str(core_props.created) if core_props.created else None,
                "modified": str(core_props.modified) if core_props.modified else None,
            }
        
        return {
            "result": {
                "content": content,
                "metadata": metadata,
                "structure": structure[:20],
                "total_length": total_length,
                "read_range": f"{start_idx}-{end_idx}",
                "truncated": end_idx < total_length
            },
            "error": None
        }
    
    except Exception as e:
        return {"result": None, "error": f"Word文档读取失败: {str(e)}"}


def _read_xlsx(file_path: str, start_idx: int = 0, max_length: int = 10000, include_metadata: bool = True) -> dict:
    """读取Excel文件"""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return {"result": None, "error": "缺少依赖: pip install openpyxl"}
    
    try:
        wb = load_workbook(file_path, data_only=True)
        
        all_content = []
        sheet_info = []
        
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            rows_data = []
            
            for row in sheet.iter_rows(values_only=True):
                row_values = [str(cell) if cell is not None else "" for cell in row]
                if any(v.strip() for v in row_values):
                    rows_data.append(" | ".join(row_values))
            
            sheet_content = f"## Sheet: {sheet_name}\n" + "\n".join(rows_data)
            all_content.append(sheet_content)
            
            sheet_info.append({
                "name": sheet_name,
                "rows": sheet.max_row,
                "cols": sheet.max_column,
                "content_preview": rows_data[:5] if rows_data else []
            })
        
        full_text = "\n\n".join(all_content)
        total_length = len(full_text)
        end_idx = min(start_idx + max_length, total_length)
        content = full_text[start_idx:end_idx]
        
        metadata = {}
        if include_metadata:
            metadata = {
                "file_type": "xlsx",
                "sheet_count": len(wb.sheetnames),
                "sheet_names": wb.sheetnames,
            }
        
        return {
            "result": {
                "content": content,
                "metadata": metadata,
                "structure": sheet_info,
                "total_length": total_length,
                "read_range": f"{start_idx}-{end_idx}",
                "truncated": end_idx < total_length
            },
            "error": None
        }
    
    except Exception as e:
        return {"result": None, "error": f"Excel文件读取失败: {str(e)}"}


def execute_read_document(tool_args: dict) -> dict:
    """执行 read_document 工具"""
    file_path = tool_args.get("file_path")
    if not file_path:
        return {"result": None, "error": "缺少 file_path 参数"}
    
    start_idx = tool_args.get("start_idx", 0)
    max_length = tool_args.get("max_length", 10000)
    include_metadata = tool_args.get("include_metadata", True)
    
    settings = get_settings_service()
    try:
        use_llm_parsing = settings.get("agent_tools:pdf:use_llm_parsing")
    except KeyError:
        use_llm_parsing = True
    
    print(f"[Tool] read_document: {file_path}")
    
    if not os.path.exists(file_path):
        return {"result": None, "error": f"文件不存在: {file_path}"}
    
    if not os.path.isfile(file_path):
        return {"result": None, "error": f"路径不是文件: {file_path}"}
    
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".pdf":
        result = _read_pdf(file_path, start_idx, max_length, include_metadata, use_llm_parsing)
    elif ext in [".docx", ".doc"]:
        if ext == ".doc":
            return {"result": None, "error": "暂不支持 .doc 格式，请转换为 .docx 格式"}
        result = _read_docx(file_path, start_idx, max_length, include_metadata)
    elif ext in [".xlsx", ".xls"]:
        if ext == ".xls":
            return {"result": None, "error": "暂不支持 .xls 格式，请转换为 .xlsx 格式"}
        result = _read_xlsx(file_path, start_idx, max_length, include_metadata)
    else:
        return {"result": None, "error": f"不支持的文件格式: {ext}。支持: .pdf, .docx, .xlsx"}
    
    if result.get("error") is None:
        print(f"[Tool] read_document 成功: {result['result'].get('total_length', 0)} 字符")
    else:
        print(f"[Tool] read_document 失败: {result['error']}")
    
    return result


DOCUMENT_TOOLS = {
    "read_document": ToolDefinition(
        name="read_document",
        description="读取PDF、Word、Excel文档内容，支持分页读取和元数据提取",
        params="file_path, start_idx, max_length, include_metadata",
        category="document",
        executor=execute_read_document
    )
}


def register_document_tools():
    """注册文档工具"""
    for tool_name, tool_def in DOCUMENT_TOOLS.items():
        ToolRegistry.register(tool_def)
