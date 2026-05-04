import os
import json
import tempfile
import shutil
from typing import Optional, Dict, Any, Tuple

from .registry import ToolDefinition, ToolRegistry
from singleton import get_settings_service


def _get_ext(file_path: str) -> str:
    return os.path.splitext(file_path)[1].lower()


def _make_result(data: Optional[dict] = None, error: Optional[str] = None) -> dict:
    return {"result": data, "error": error}


# ============================================================
# PDF 操作 (r/w/a/u)
# ============================================================

def _pdf_read(file_path: str, start_idx: int = 0, max_length: int = 10000,
              include_metadata: bool = True, use_llm_parsing: bool = True) -> dict:
    try:
        import pypdf
    except ImportError:
        return _make_result(error="缺少依赖: pip install pypdf")
    
    try:
        if use_llm_parsing:
            try:
                import pymupdf4llm
            except ImportError:
                return _make_result(error="缺少依赖: pip install pymupdf4llm")
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
                        "parsing_mode": "llm" if use_llm_parsing else "fast",
                    }
            except Exception:
                metadata = {"file_type": "pdf", "page_count": "unknown"}
        
        return _make_result({
            "content": content,
            "metadata": metadata,
            "total_length": total_length,
            "read_range": f"{start_idx}-{end_idx}",
            "truncated": end_idx < total_length
        })
    except Exception as e:
        return _make_result(error=f"PDF读取失败: {str(e)}")


def _pdf_write(file_path: str, content: str, metadata: Optional[dict] = None) -> dict:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as pdf_canvas
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return _make_result(error="缺少依赖: pip install reportlab")
    
    try:
        c = pdf_canvas.Canvas(file_path, pagesize=A4)
        width, height = A4
        
        meta = metadata or {}
        if meta.get("title"):
            c.setTitle(meta["title"])
        if meta.get("author"):
            c.setAuthor(meta["author"])
        
        lines = content.split("\n")
        y = height - 40 * mm
        
        for line in lines:
            if y < 40 * mm:
                c.showPage()
                y = height - 40 * mm
            
            if line.startswith("# "):
                c.setFont("Helvetica-Bold", 18)
                c.drawString(20 * mm, y, line[2:].strip())
                y -= 12 * mm
            elif line.startswith("## "):
                c.setFont("Helvetica-Bold", 14)
                c.drawString(20 * mm, y, line[3:].strip())
                y -= 10 * mm
            elif line.startswith("### "):
                c.setFont("Helvetica-Bold", 12)
                c.drawString(20 * mm, y, line[4:].strip())
                y -= 8 * mm
            else:
                c.setFont("Helvetica", 10)
                
                max_width = width - 40 * mm
                
                if c.stringWidth(line, "Helvetica", 10) > max_width:
                    words = line.split()
                    current_line = ""
                    for word in words:
                        test = current_line + " " + word if current_line else word
                        if c.stringWidth(test, "Helvetica", 10) <= max_width:
                            current_line = test
                        else:
                            c.drawString(20 * mm, y, current_line)
                            y -= 6 * mm
                            if y < 40 * mm:
                                c.showPage()
                                y = height - 40 * mm
                            current_line = word
                    if current_line:
                        c.drawString(20 * mm, y, current_line)
                        y -= 6 * mm
                else:
                    c.drawString(20 * mm, y, line)
                    y -= 6 * mm
        
        c.save()
        return _make_result({"message": f"PDF创建成功: {file_path}", "pages": c.getPageNumber() if hasattr(c, '_pageNumber') else "unknown"})
    except Exception as e:
        return _make_result(error=f"PDF写入失败: {str(e)}")


def _pdf_append(file_path: str, content: str) -> dict:
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as pdf_canvas
        from reportlab.lib.units import mm
    except ImportError:
        return _make_result(error="缺少依赖: pip install pypdf reportlab")
    
    try:
        temp_pdf = tempfile.mktemp(suffix=".append.pdf")
        c = pdf_canvas.Canvas(temp_pdf, pagesize=A4)
        width, height = A4
        
        lines = content.split("\n")
        y = height - 40 * mm
        
        for line in lines:
            if y < 40 * mm:
                c.showPage()
                y = height - 40 * mm
            
            c.setFont("Helvetica", 10)
            
            max_width = width - 40 * mm
            if c.stringWidth(line, "Helvetica", 10) > max_width:
                words = line.split()
                current_line = ""
                for word in words:
                    test = current_line + " " + word if current_line else word
                    if c.stringWidth(test, "Helvetica", 10) <= max_width:
                        current_line = test
                    else:
                        c.drawString(20 * mm, y, current_line)
                        y -= 6 * mm
                        if y < 40 * mm:
                            c.showPage()
                            y = height - 40 * mm
                        current_line = word
                if current_line:
                    c.drawString(20 * mm, y, current_line)
                    y -= 6 * mm
            else:
                c.drawString(20 * mm, y, line)
                y -= 6 * mm
        
        c.save()
        
        writer = PdfWriter()
        reader = PdfReader(file_path)
        for page in reader.pages:
            writer.add_page(page)
        
        append_reader = PdfReader(temp_pdf)
        for page in append_reader.pages:
            writer.add_page(page)
        
        with open(file_path, "wb") as f:
            writer.write(f)
        
        os.unlink(temp_pdf)
        return _make_result({"message": f"PDF追加成功，总页数: {len(writer.pages)}"})
    except Exception as e:
        if os.path.exists(temp_pdf):
            os.unlink(temp_pdf)
        return _make_result(error=f"PDF追加失败: {str(e)}")


def _pdf_update(file_path: str, target: str, content: Optional[str] = None,
                field: Optional[str] = None) -> dict:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return _make_result(error="缺少依赖: pip install pypdf")
    
    try:
        if field == "metadata":
            reader = PdfReader(file_path)
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            
            meta_data = {}
            if isinstance(content, str):
                try:
                    meta_data = json.loads(content)
                except json.JSONDecodeError:
                    meta_data = {"info_string": content}
            elif isinstance(content, dict):
                meta_data = content
            
            if meta_data:
                from pypdf.generic import NameObject, TextStringObject, create_string_object
                info_dict = {}
                for k, v in meta_data.items():
                    info_dict[NameObject(f"/{k}")] = create_string_object(str(v))
                if info_dict:
                    writer.add_metadata(info_dict)
            
            with open(file_path, "wb") as f:
                writer.write(f)
            return _make_result({"message": "PDF元数据更新成功"})
        
        return _make_result(error=f"不支持的更新操作: field={field}, 支持修改元数据(metadata)")
    except Exception as e:
        return _make_result(error=f"PDF更新失败: {str(e)}")


# ============================================================
# DOCX / DOC 操作 (r/w/a/u)
# ============================================================

def _convert_doc_to_docx(file_path: str) -> Optional[str]:
    try:
        import subprocess
        temp_docx = tempfile.mktemp(suffix=".docx")
        
        try:
            from docx2python import docx2python
            docx2python(file_path, temp_docx)
            return temp_docx
        except ImportError:
            pass
        
        try:
            result = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "docx", "--outdir",
                 os.path.dirname(temp_docx), file_path],
                capture_output=True, timeout=30
            )
            if result.returncode == 0:
                converted = file_path.rsplit(".", 1)[0] + ".docx"
                if os.path.exists(converted):
                    shutil.move(converted, temp_docx)
                    return temp_docx
        except FileNotFoundError:
            pass
        
        return None
    except Exception:
        return None


def _docx_read(file_path: str, start_idx: int = 0, max_length: int = 10000,
               include_metadata: bool = True) -> dict:
    try:
        from docx import Document
    except ImportError:
        return _make_result(error="缺少依赖: pip install python-docx")
    
    try:
        actual_path = file_path
        cleanup = False
        
        if _get_ext(file_path) == ".doc":
            converted = _convert_doc_to_docx(file_path)
            if not converted:
                return _make_result(error=".doc格式转换失败，请安装LibreOffice或docx2python")
            actual_path = converted
            cleanup = True
        
        doc = Document(actual_path)
        
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
                "file_type": _get_ext(file_path).lstrip("."),
                "paragraph_count": len(paragraphs),
                "author": core_props.author,
                "title": core_props.title,
                "subject": core_props.subject,
            }
        
        if cleanup and actual_path != file_path:
            os.unlink(actual_path)
        
        return _make_result({
            "content": content,
            "metadata": metadata,
            "structure": structure[:20],
            "total_length": total_length,
            "read_range": f"{start_idx}-{end_idx}",
            "truncated": end_idx < total_length
        })
    except Exception as e:
        return _make_result(error=f"Word文档读取失败: {str(e)}")


def _markdown_to_docx_content(markdown_text: str, doc) -> None:
    try:
        import re
    except ImportError:
        pass
    
    lines = markdown_text.split("\n")
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        if line.startswith("# "):
            heading = doc.add_heading(level=1)
            heading.add_run(line[2:].strip())
        elif line.startswith("## "):
            heading = doc.add_heading(level=2)
            heading.add_run(line[3:].strip())
        elif line.startswith("### "):
            heading = doc.add_heading(level=3)
            heading.add_run(line[4:].strip())
        elif line.startswith("#### "):
            heading = doc.add_heading(level=4)
            heading.add_run(line[5:].strip())
        elif line.startswith("---") or line.startswith("***"):
            pass
        elif line.startswith("- ") or line.startswith("* "):
            items = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("* ")):
                items.append(lines[i][2:].strip())
                i += 1
            for item in items:
                p = doc.add_paragraph(item, style='List Bullet')
            continue
        elif re.match(r'^\d+\.\s', line):
            items = []
            while i < len(lines) and re.match(r'^\d+\.\s', lines[i]):
                items.append(re.sub(r'^\d+\.\s', '', lines[i]).strip())
                i += 1
            for item in items:
                p = doc.add_paragraph(item, style='List Number')
            continue
        elif line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            
            if len(table_lines) >= 3:
                header_line = table_lines[0]
                headers = [cell.strip() for cell in header_line.strip("|").split("|")]
                
                rows = []
                for tl in table_lines[2:]:
                    if "---" not in tl:
                        row_cells = [cell.strip() for cell in tl.strip("|").split("|")]
                        rows.append(row_cells)
                
                if headers:
                    table = doc.add_table(rows=len(rows)+1, cols=len(headers))
                    table.style = 'Table Grid'
                    
                    for j, h in enumerate(headers):
                        table.rows[0].cells[j].text = h
                    
                    for r_idx, row in enumerate(rows):
                        for c_idx, cell in enumerate(row):
                            if c_idx < len(table.rows[r_idx+1].cells):
                                table.rows[r_idx+1].cells[c_idx].text = cell
            continue
        elif line.strip():
            doc.add_paragraph(line)
        
        i += 1


def _docx_write(file_path: str, content: str, metadata: Optional[dict] = None) -> dict:
    try:
        from docx import Document
    except ImportError:
        return _make_result(error="缺少依赖: pip install python-docx")
    
    try:
        ext = _get_ext(file_path)
        target_path = file_path if ext == ".docx" else tempfile.mktemp(suffix=".docx")
        
        doc = Document()
        
        meta = metadata or {}
        if meta.get("author"):
            doc.core_properties.author = meta["author"]
        if meta.get("title"):
            doc.core_properties.title = meta["title"]
        if meta.get("subject"):
            doc.core_properties.subject = meta["subject"]
        
        _markdown_to_docx_content(content, doc)
        
        doc.save(target_path)
        
        if ext == ".doc":
            try:
                import subprocess
                result = subprocess.run(
                    ["libreoffice", "--headless", "--convert-to", "doc",
                     "--outdir", os.path.dirname(os.path.abspath(file_path)), target_path],
                    capture_output=True, timeout=30
                )
                os.unlink(target_path)
                if result.returncode == 0:
                    return _make_result({"message": f"DOC文档创建成功: {file_path}"})
                else:
                    return _make_result(error=f"DOC转换失败: {result.stderr.decode()}")
            except FileNotFoundError:
                os.unlink(target_path)
                return _make_result(error="创建DOC需要LibreOffice支持，已生成DOCX版本")
        
        return _make_result({"message": f"Word文档创建成功: {file_path}"})
    except Exception as e:
        return _make_result(error=f"Word文档写入失败: {str(e)}")


def _docx_append(file_path: str, content: str) -> dict:
    try:
        from docx import Document
    except ImportError:
        return _make_result(error="缺少依赖: pip install python-docx")
    
    try:
        ext = _get_ext(file_path)
        actual_path = file_path
        
        if ext == ".doc":
            converted = _convert_doc_to_docx(file_path)
            if converted:
                actual_path = converted
            else:
                return _make_result(error="无法追加DOC格式，请转换为DOCX")
        
        doc = Document(actual_path)
        _markdown_to_docx_content(content, doc)
        doc.save(actual_path)
        
        if ext == ".doc":
            try:
                import subprocess
                result = subprocess.run(
                    ["libreoffice", "--headless", "--convert-to", "doc",
                     "--outdir", os.path.dirname(os.path.abspath(file_path)), actual_path],
                    capture_output=True, timeout=30
                )
                os.unlink(actual_path)
                if result.returncode == 0:
                    return _make_result({"message": "DOC追加成功"})
            except (FileNotFoundError, Exception):
                return _make_result(error="DOC转换失败")
        
        return _make_result({"message": "Word文档追加成功"})
    except Exception as e:
        return _make_result(error=f"Word文档追加失败: {str(e)}")


def _docx_update(file_path: str, target: str, content: str, field: Optional[str] = None) -> dict:
    try:
        from docx import Document
    except ImportError:
        return _make_result(error="缺少依赖: pip install python-docx")
    
    try:
        ext = _get_ext(file_path)
        actual_path = file_path
        cleanup = False
        
        if ext == ".doc":
            converted = _convert_doc_to_docx(file_path)
            if not converted:
                return _make_result(error="无法修改DOC格式")
            actual_path = converted
            cleanup = True
        
        doc = Document(actual_path)
        
        if field == "paragraph":
            try:
                para_idx = int(target)
                if 0 <= para_idx < len(doc.paragraphs):
                    para = doc.paragraphs[para_idx]
                    for run in para.runs:
                        run.text = ""
                    if para.runs:
                        para.runs[0].text = content
                    else:
                        para.add_run(content)
                    doc.save(actual_path)
                    
                    if cleanup:
                        _save_as_doc(actual_path, file_path)
                        os.unlink(actual_path)
                    
                    return _make_result({"message": f"段落{para_idx}更新成功"})
                else:
                    return _make_result(error=f"段落索引超出范围(0-{len(doc.paragraphs)-1})")
            except ValueError:
                return _make_result(error="target必须是段落索引数字")
        
        elif field == "metadata":
            meta_fields = json.loads(content) if isinstance(content, str) else content
            props = doc.core_properties
            for k, v in meta_fields.items():
                if hasattr(props, k):
                    setattr(props, k, v)
            doc.save(actual_path)
            
            if cleanup:
                _save_as_doc(actual_path, file_path)
                os.unlink(actual_path)
            
            return _make_result({"message": "Word元数据更新成功"})
        
        if cleanup and os.path.exists(actual_path):
            os.unlink(actual_path)
        return _make_result(error=f"不支持的操作: field={field}, 支持 paragraph/metadata")
    except Exception as e:
        return _make_result(error=f"Word文档更新失败: {str(e)}")


def _save_as_doc(docx_path: str, output_doc: str) -> bool:
    try:
        import subprocess
        subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "doc",
             "--outdir", os.path.dirname(os.path.abspath(output_doc)), docx_path],
            capture_output=True, timeout=30
        )
        return True
    except Exception:
        return False


# ============================================================
# Excel XLS/XLSX 操作 (r/w/a/u)
# ============================================================

def _excel_read(file_path: str, start_idx: int = 0, max_length: int = 10000,
                include_metadata: bool = True) -> dict:
    try:
        import pandas as pd
    except ImportError:
        return _make_result(error="缺少依赖: pip install pandas")
    
    try:
        ext = _get_ext(file_path)
        
        if ext == ".xls":
            try:
                import xlrd
            except ImportError:
                return _make_result(error="缺少依赖: pip install xlrd")
        
        xl_file = pd.ExcelFile(file_path)
        sheet_names = xl_file.sheet_names
        
        all_content = []
        sheet_info = []
        
        for sheet_name in sheet_names:
            df = pd.read_excel(xl_file, sheet_name=sheet_name, header=None)
            
            rows_data = []
            for _, row in df.iterrows():
                row_values = [str(cell) if pd.notna(cell) else "" for cell in row]
                if any(v.strip() for v in row_values):
                    rows_data.append(" | ".join(row_values))
            
            sheet_content = f"## Sheet: {sheet_name}\n" + "\n".join(rows_data)
            all_content.append(sheet_content)
            
            sheet_info.append({
                "name": sheet_name,
                "rows": len(df),
                "cols": len(df.columns),
            })
        
        full_text = "\n\n".join(all_content)
        total_length = len(full_text)
        end_idx = min(start_idx + max_length, total_length)
        content = full_text[start_idx:end_idx]
        
        metadata = {}
        if include_metadata:
            metadata = {
                "file_type": ext.lstrip("."),
                "sheet_count": len(sheet_names),
                "sheet_names": sheet_names,
            }
        
        return _make_result({
            "content": content,
            "metadata": metadata,
            "structure": sheet_info,
            "total_length": total_length,
            "read_range": f"{start_idx}-{end_idx}",
            "truncated": end_idx < total_length
        })
    except Exception as e:
        return _make_result(error=f"Excel读取失败: {str(e)}")


def _excel_write(file_path: str, data: Dict[str, list], metadata: Optional[dict] = None) -> dict:
    ext = _get_ext(file_path)
    
    if ext == ".xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            return _make_result(error="缺少依赖: pip install openpyxl")
        
        try:
            wb = Workbook()
            default_sheet = wb.active
            
            for idx, (sheet_name, rows) in enumerate(data.items()):
                if idx == 0:
                    default_sheet.title = sheet_name
                    ws = default_sheet
                else:
                    ws = wb.create_sheet(title=sheet_name)
                
                for row_data in rows:
                    ws.append(row_data)
            
            wb.save(file_path)
            return _make_result({"message": f"XLSX创建成功: {file_path}", "sheets": list(data.keys())})
        except Exception as e:
            return _make_result(error=f"XLSX写入失败: {str(e)}")
    
    elif ext == ".xls":
        try:
            import xlwt
        except ImportError:
            return _make_result(error="缺少依赖: pip install xlwt")
        
        try:
            wb = xlwt.Workbook()
            
            for idx, (sheet_name, rows) in enumerate(data.items()):
                ws = wb.add_sheet(sheet_name[:31])
                
                for row_idx, row_data in enumerate(rows):
                    for col_idx, value in enumerate(row_data):
                        ws.write(row_idx, col_idx, value)
            
            wb.save(file_path)
            return _make_result({"message": f"XLS创建成功: {file_path}", "sheets": list(data.keys())})
        except Exception as e:
            return _make_result(error=f"XLS写入失败: {str(e)}")
    
    return _make_result(error=f"不支持的Excel格式: {ext}")


def _excel_append(file_path: str, data: Dict[str, list], target_sheet: Optional[str] = None) -> dict:
    ext = _get_ext(file_path)
    
    if ext == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError:
            return _make_result(error="缺少依赖: pip install openpyxl")
        
        try:
            wb = load_workbook(file_path)
            
            for sheet_name, rows in data.items():
                actual_sheet = sheet_name
                if target_sheet:
                    actual_sheet = target_sheet
                
                if actual_sheet in wb.sheetnames:
                    ws = wb[actual_sheet]
                    start_row = ws.max_row + 1
                    
                    for row_data in rows:
                        for col_idx, value in enumerate(row_data, 1):
                            ws.cell(row=start_row, column=col_idx, value=value)
                        start_row += 1
                else:
                    ws = wb.create_sheet(title=actual_sheet)
                    for row_data in rows:
                        ws.append(row_data)
            
            wb.save(file_path)
            return _make_result({"message": "Excel数据追加成功"})
        except Exception as e:
            return _make_result(error=f"Excel追加失败: {str(e)}")
    
    elif ext == ".xls":
        try:
            import xlutils
            import xlrd
            import xlwt
        except ImportError:
            return _make_result(error="缺少依赖: pip install xlutils xlrd xlwt")
        
        try:
            rb = xlrd.open_workbook(file_path, formatting_info=True)
            wb = xlutils.copy.copy(rb)
            
            for sheet_name, rows in data.items():
                actual_sheet = sheet_name
                if target_sheet:
                    actual_sheet = target_sheet
                
                if actual_sheet in rb.sheet_names():
                    ws = wb.get_sheet(rb.sheet_names().index(actual_sheet))
                    start_row = ws.last_used_row + 1 if hasattr(ws, 'last_used_row') else rb.sheet_by_name(actual_sheet).nrows
                    
                    for row_idx, row_data in enumerate(rows):
                        for col_idx, value in enumerate(row_data):
                            ws.write(start_row + row_idx, col_idx, value)
                else:
                    ws = wb.add_sheet(actual_sheet)
                    for row_idx, row_data in enumerate(rows):
                        for col_idx, value in enumerate(row_data):
                            ws.write(row_idx, col_idx, value)
            
            wb.save(file_path)
            return _make_result({"message": "Excel数据追加成功"})
        except Exception as e:
            return _make_result(error=f"Excel追加失败: {str(e)}")
    
    return _make_result(error=f"不支持的格式: {ext}")


def _excel_update(file_path: str, target: str, content: Any,
                  sheet_name: Optional[str] = None) -> dict:
    ext = _get_ext(file_path)
    
    try:
        cell_match = target.replace(" ", "").upper()
        import re
        match = re.match(r'([A-Z]+)(\d+)', cell_match)
        if not match:
            return _make_result(error="target格式错误，应为单元格坐标如'A1', 'B3'")
        
        col_str = match.group(1)
        row_num = int(match.group(2))
        
        col_num = 0
        for ch in col_str:
            col_num = col_num * 26 + (ord(ch) - ord('A') + 1)
    except Exception:
        return _make_result(error="target解析失败")
    
    if ext == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError:
            return _make_result(error="缺少依赖: pip install openpyxl")
        
        try:
            wb = load_workbook(file_path)
            ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
            ws.cell(row=row_num, column=col_num, value=content)
            wb.save(file_path)
            return _make_result({"message": f"单元格{target}更新为: {content}"})
        except Exception as e:
            return _make_result(error=f"XLSX更新失败: {str(e)}")
    
    elif ext == ".xls":
        try:
            import xlutils
            import xlrd
        except ImportError:
            return _make_result(error="缺少依赖: pip install xlutils xlrd")
        
        try:
            rb = xlrd.open_workbook(file_path, formatting_info=True)
            wb = xlutils.copy.copy(rb)
            
            s_idx = rb.sheet_names().index(sheet_name) if sheet_name and sheet_name in rb.sheet_names() else 0
            ws = wb.get_sheet(s_idx)
            ws.write(row_num - 1, col_num - 1, content)
            wb.save(file_path)
            return _make_result({"message": f"单元格{target}更新为: {content}"})
        except Exception as e:
            return _make_result(error=f"XLS更新失败: {str(e)}")
    
    return _make_result(error=f"不支持的格式: {ext}")


# ============================================================
# 统一入口：execute_document (类似 fopen)
# ============================================================

def execute_document(tool_args: dict) -> dict:
    operation = tool_args.get("operation")
    file_path = tool_args.get("file_path")
    
    if not operation:
        return _make_result(error="缺少 operation 参数 (r|w|a|u)")
    
    if not file_path:
        return _make_result(error="缺少 file_path 参数")
    
    valid_ops = {"r", "w", "a", "u"}
    if operation not in valid_ops:
        return _make_result(error=f"无效操作类型: {operation}，支持: {'|'.join(sorted(valid_ops))}")
    
    print(f"[Tool] document [{operation}] {file_path}")
    
    if operation != "w":
        if not os.path.exists(file_path):
            return _make_result(error=f"文件不存在: {file_path}")
        if not os.path.isfile(file_path):
            return _make_result(error=f"路径不是文件: {file_path}")
    
    ext = _get_ext(file_path)
    supported_formats = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
    
    if ext not in supported_formats:
        return _make_result(error=f"不支持的格式: {ext}，支持: {', '.join(sorted(supported_formats))}")
    
    settings = get_settings_service()
    try:
        use_llm_parsing = settings.get("agent_tools:pdf:use_llm_parsing")
    except KeyError:
        use_llm_parsing = True
    
    # ---- READ ----
    if operation == "r":
        start_idx = tool_args.get("start_idx", 0)
        max_length = tool_args.get("max_length", 10000)
        include_metadata = tool_args.get("include_metadata", True)
        
        if ext == ".pdf":
            result = _pdf_read(file_path, start_idx, max_length, include_metadata, use_llm_parsing)
        elif ext in {".doc", ".docx"}:
            result = _docx_read(file_path, start_idx, max_length, include_metadata)
        elif ext in {".xls", ".xlsx"}:
            result = _excel_read(file_path, start_idx, max_length, include_metadata)
        else:
            result = _make_result(error=f"读取暂不支持: {ext}")
    
    # ---- WRITE ----
    elif operation == "w":
        content = tool_args.get("content", "")
        data = tool_args.get("data")
        metadata = tool_args.get("metadata")
        
        if ext == ".pdf":
            if not content:
                return _make_result(error="PDF写入需要content参数")
            result = _pdf_write(file_path, content, metadata)
        elif ext in {".doc", ".docx"}:
            if not content:
                return _make_result(error="Word写入需要content参数(Markdown文本)")
            result = _docx_write(file_path, content, metadata)
        elif ext in {".xls", ".xlsx"}:
            if not data:
                return _make_result(error="Excel写入需要data参数(JSON数组)")
            result = _excel_write(file_path, data, metadata)
        else:
            result = _make_result(error=f"写入暂不支持: {ext}")
    
    # ---- APPEND ----
    elif operation == "a":
        content = tool_args.get("content", "")
        data = tool_args.get("data")
        position = tool_args.get("position", "end")
        
        if ext == ".pdf":
            if not content:
                return _make_result(error="PDF追加需要content参数")
            result = _pdf_append(file_path, content)
        elif ext == ".docx":
            if not content:
                return _make_result(error="DOCX追加需要content参数")
            result = _docx_append(file_path, content)
        elif ext == ".doc":
            result = _make_result(error="DOC格式建议使用write模式覆盖或转为DOCX")
        elif ext in {".xls", ".xlsx"}:
            if not data:
                return _make_result(error="Excel追加需要data参数")
            target_sheet = tool_args.get("target") or tool_args.get("sheet_name")
            result = _excel_append(file_path, data, target_sheet)
        else:
            result = _make_result(error=f"追加暂不支持: {ext}")
    
    # ---- UPDATE ----
    elif operation == "u":
        target = tool_args.get("target")
        content = tool_args.get("content")
        field = tool_args.get("field")
        
        if not target:
            return _make_result(error="update操作需要target参数(定位信息)")
        
        if ext == ".pdf":
            result = _pdf_update(file_path, target, content, field or "metadata")
        elif ext in {".doc", ".docx"}:
            result = _docx_update(file_path, target, content, field or "paragraph")
        elif ext in {".xls", ".xlsx"}:
            if not content:
                return _make_result(error="Excel update需要content参数(新值)")
            sheet_name = tool_args.get("sheet_name")
            result = _excel_update(file_path, target, content, sheet_name)
        else:
            result = _make_result(error=f"更新暂不支持: {ext}")
    
    else:
        result = _make_result(error=f"未知操作: {operation}")
    
    if result.get("error") is None:
        print(f"[Tool] document [{operation}] 成功: {file_path}")
    else:
        print(f"[Tool] document [{operation}] 失败: {result['error']}")
    
    return result


DOCUMENT_TOOLS = {
    "document": ToolDefinition(
        name="document",
        description="统一文档操作工具(类似fopen)，支持PDF/DOC/DOCX/XLS/XLSX的读写追加修改。操作类型: r=读 w=写 a=追加 u=修改",
        params='document:{"operation":"(必填)r|w|a|u","file_path":"(必填)文档路径","content":"(文本内容)","data":"(Excel用JSON数组)","target":"(update定位)","field":"(字段类型)","metadata":"(文档元数据)","start_idx":"(起始位置)","max_length":"(最大长度)","include_metadata":"(含元数据)"}',
        category="document",
        executor=execute_document
    ),
    "read_document": ToolDefinition(
        name="read_document",
        description="[兼容]读取PDF、Word、Excel文档内容（推荐使用document工具）",
        params='read_document:{"file_path":"(文档路径)","start_idx":"(起始索引)","max_length":"(最大长度)","include_metadata":"(含元数据)"}',
        category="document",
        executor=lambda args: execute_document({**args, "operation": "r"})
    )
}


def register_document_tools():
    for tool_name, tool_def in DOCUMENT_TOOLS.items():
        ToolRegistry.register(tool_def)
