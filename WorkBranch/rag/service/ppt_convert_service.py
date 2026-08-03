from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_LEGACY_PPT_MIMES = frozenset(
    {
        "application/vnd.ms-powerpoint",
        "application/mspowerpoint",
        "application/ms-powerpoint",
        "application/x-ms-ppt",
    }
)


class PptConvertError(RuntimeError):
    """Raised when a legacy .ppt file cannot be converted to .pptx."""


def is_legacy_ppt(filename: str, mime: str | None = None) -> bool:
    lower = (filename or "").lower().replace("\\", "/")
    base = lower.rsplit("/", 1)[-1]
    if base.endswith(".pptx"):
        return False
    if base.endswith(".ppt"):
        return True
    if mime and mime.lower().split(";", 1)[0].strip() in _LEGACY_PPT_MIMES:
        return True
    return False


def pptx_display_name(filename: str) -> str:
    path = Path((filename or "document.ppt").replace("\\", "/"))
    if path.suffix.lower() == ".ppt":
        return path.with_suffix(".pptx").name
    if path.suffix.lower() == ".pptx":
        return path.name
    return f"{path.name}.pptx"


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
        raise PptConvertError("未找到 LibreOffice（soffice）。请安装 LibreOffice 或设置环境变量 LIBREOFFICE_PATH。")

    safe_name = Path(source_filename.replace("\\", "/")).name or "upload.ppt"
    if not safe_name.lower().endswith(".ppt"):
        safe_name = f"{Path(safe_name).stem}.ppt"

    with tempfile.TemporaryDirectory(prefix="rag-ppt-convert-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / safe_name
        src.write_bytes(content)

        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nologo",
            "--convert-to",
            "pptx",
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
            raise PptConvertError(f"LibreOffice 转换失败（exit={proc.returncode}）: {stderr[:500]}")

        out = tmp_dir / f"{src.stem}.pptx"
        if not out.is_file() or out.stat().st_size == 0:
            raise PptConvertError("LibreOffice 未生成有效的 .pptx 文件")

        return out.read_bytes()


def _convert_via_powerpoint_com(content: bytes, source_filename: str) -> bytes:
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise PptConvertError("未安装 pywin32，无法使用本机 PowerPoint 转换") from exc

    safe_name = Path(source_filename.replace("\\", "/")).name or "upload.ppt"
    if not safe_name.lower().endswith(".ppt"):
        safe_name = f"{Path(safe_name).stem}.ppt"

    with tempfile.TemporaryDirectory(prefix="rag-ppt-convert-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / safe_name
        dst = tmp_dir / f"{src.stem}.pptx"
        src.write_bytes(content)

        pythoncom.CoInitialize()
        app = None
        pres = None
        try:
            app = win32com.client.DispatchEx("PowerPoint.Application")
            pres = app.Presentations.Open(str(src), ReadOnly=True, WithWindow=False)
            # 24 = ppSaveAsOpenXMLPresentation (.pptx)
            pres.SaveAs(str(dst), 24)
            pres.Close()
            app.Quit()
            app = None
            pres = None
        except Exception as exc:
            raise PptConvertError(f"本机 PowerPoint 转换失败: {exc}") from exc
        finally:
            if pres is not None:
                try:
                    pres.Close()
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()

        if not dst.is_file() or dst.stat().st_size == 0:
            raise PptConvertError("PowerPoint 未生成有效的 .pptx 文件")
        return dst.read_bytes()


def convert_ppt_bytes_to_pptx(content: bytes, source_filename: str) -> bytes:
    """将旧版 .ppt 字节流转为 .pptx。优先 LibreOffice，Windows 下回退本机 PowerPoint。"""
    errors: list[str] = []
    try:
        return _convert_via_libreoffice(content, source_filename)
    except PptConvertError as exc:
        errors.append(str(exc))

    if os.name == "nt":
        try:
            return _convert_via_powerpoint_com(content, source_filename)
        except PptConvertError as exc:
            errors.append(str(exc))

    hint = "；".join(errors) if errors else "无可用转换器"
    raise PptConvertError(
        f"无法将 .ppt 转为 .pptx：{hint}。"
        "请安装 LibreOffice，或在 Windows 上安装 Microsoft PowerPoint + pywin32，或手动另存为 .pptx 后上传。"
    )
