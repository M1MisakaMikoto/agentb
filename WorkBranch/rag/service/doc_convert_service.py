from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_LEGACY_DOC_MIMES = frozenset(
    {
        "application/msword",
        "application/ms-word",
        "application/vnd.ms-word",
    }
)


class DocConvertError(RuntimeError):
    """Raised when a legacy .doc file cannot be converted to .docx."""


def is_legacy_doc(filename: str, mime: str | None = None) -> bool:
    lower = (filename or "").lower().replace("\\", "/")
    base = lower.rsplit("/", 1)[-1]
    if base.endswith(".docx"):
        return False
    if base.endswith(".doc"):
        return True
    if mime and mime.lower().split(";", 1)[0].strip() in _LEGACY_DOC_MIMES:
        return True
    return False


def docx_display_name(filename: str) -> str:
    path = Path((filename or "document.doc").replace("\\", "/"))
    if path.suffix.lower() == ".doc":
        return path.with_suffix(".docx").name
    if path.suffix.lower() == ".docx":
        return path.name
    return f"{path.name}.docx"


def _soffice_candidates() -> list[str]:
    env_path = (os.environ.get("LIBREOFFICE_PATH") or os.environ.get("SOFFICE_PATH") or "").strip()
    candidates: list[str] = []
    if env_path:
        candidates.append(env_path)
    candidates.extend(
        [
            "soffice",
            "libreoffice",
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    )
    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _resolve_soffice() -> str | None:
    for candidate in _soffice_candidates():
        if os.path.isabs(candidate) or os.sep in candidate or (os.name == "nt" and ":" in candidate):
            if Path(candidate).is_file():
                return candidate
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _convert_via_libreoffice(content: bytes, source_filename: str) -> bytes:
    soffice = _resolve_soffice()
    if not soffice:
        raise DocConvertError("未找到 LibreOffice（soffice）。请安装 LibreOffice 或设置环境变量 LIBREOFFICE_PATH。")

    safe_name = Path(source_filename.replace("\\", "/")).name or "upload.doc"
    if not safe_name.lower().endswith(".doc"):
        safe_name = f"{Path(safe_name).stem}.doc"

    with tempfile.TemporaryDirectory(prefix="rag-doc-convert-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / safe_name
        src.write_bytes(content)

        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nologo",
            "--convert-to",
            "docx",
            "--outdir",
            str(tmp_dir),
            str(src),
        ]
        run_kwargs: dict = {
            "check": False,
            "capture_output": True,
            "timeout": 180,
            "text": True,
        }
        if os.name == "nt":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        proc = subprocess.run(cmd, **run_kwargs)
        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            raise DocConvertError(f"LibreOffice 转换失败（exit={proc.returncode}）: {stderr[:500]}")

        out = tmp_dir / f"{src.stem}.docx"
        if not out.is_file() or out.stat().st_size == 0:
            raise DocConvertError("LibreOffice 未生成有效的 .docx 文件")

        return out.read_bytes()


def _convert_via_word_com(content: bytes, source_filename: str) -> bytes:
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise DocConvertError("未安装 pywin32，无法使用本机 Word 转换") from exc

    safe_name = Path(source_filename.replace("\\", "/")).name or "upload.doc"
    if not safe_name.lower().endswith(".doc"):
        safe_name = f"{Path(safe_name).stem}.doc"

    with tempfile.TemporaryDirectory(prefix="rag-doc-convert-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / safe_name
        dst = tmp_dir / f"{src.stem}.docx"
        src.write_bytes(content)

        pythoncom.CoInitialize()
        word = None
        doc = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            doc = word.Documents.Open(str(src), ReadOnly=True)
            # 16 = wdFormatXMLDocument (.docx)
            doc.SaveAs2(str(dst), FileFormat=16)
            doc.Close(False)
            word.Quit()
            word = None
            doc = None
        except Exception as exc:
            raise DocConvertError(f"本机 Word 转换失败: {exc}") from exc
        finally:
            if doc is not None:
                try:
                    doc.Close(False)
                except Exception:
                    pass
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()

        if not dst.is_file() or dst.stat().st_size == 0:
            raise DocConvertError("Word 未生成有效的 .docx 文件")
        return dst.read_bytes()


def convert_doc_bytes_to_docx(content: bytes, source_filename: str) -> bytes:
    """将旧版 .doc 字节流转为 .docx。优先 LibreOffice，Windows 下回退本机 Word。"""
    errors: list[str] = []
    try:
        return _convert_via_libreoffice(content, source_filename)
    except DocConvertError as exc:
        errors.append(str(exc))

    if os.name == "nt":
        try:
            return _convert_via_word_com(content, source_filename)
        except DocConvertError as exc:
            errors.append(str(exc))

    hint = "；".join(errors) if errors else "无可用转换器"
    raise DocConvertError(
        f"无法将 .doc 转为 .docx：{hint}。"
        "请安装 LibreOffice，或在 Windows 上安装 Microsoft Word + pywin32，或手动另存为 .docx 后上传。"
    )
