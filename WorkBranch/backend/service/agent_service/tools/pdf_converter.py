"""PDF 转换器：docx → tex → xelatex → PDF

转换链：
1. 已有 docx（由 document_tools._docx_write 生成）
2. pandoc 用自定义最简模板将 docx 转为 tex（绕过默认模板的 unicode-math 依赖）
3. xelatex 编译 tex 为 PDF

TinyTeX 安装在项目内 setup/tinytex/ 目录，不污染用户目录。
首次使用自动下载（约 244MB），超时 600s 后抛错含手动安装说明。
不依赖 pytinytex 库，自己管理下载/解压/编译。
"""
import os
import sys
import threading
import tempfile
import zipfile
import shutil
import subprocess
import urllib.request

TINYTEX_DOWNLOAD_TIMEOUT = 600  # 秒
TINYTEX_DOWNLOAD_URL = "https://github.com/rstudio/tinytex-releases/releases/download/v2026.06/TinyTeX-v2026.06.zip"


def _get_project_root() -> str:
    """返回项目根目录（pdf_converter.py 上溯 5 层）"""
    # tools/ -> agent_service/ -> service/ -> backend/ -> WorkBranch/ -> 项目根
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))


def _get_tinytex_dir() -> str:
    """返回项目内 TinyTeX 安装目录"""
    return os.path.join(_get_project_root(), "setup", "tinytex")


def _get_xelatex_path() -> str:
    """返回 xelatex 可执行文件路径（Windows）"""
    return os.path.join(_get_tinytex_dir(), "TinyTeX", "bin", "windows", "xelatex.exe")


def _get_template_path() -> str:
    """返回 simple_template.tex 的绝对路径（与本文件同目录）"""
    return os.path.join(os.path.dirname(__file__), "simple_template.tex")


def _ensure_tinytex() -> str:
    """确保 TinyTeX 已安装到项目内目录，返回 xelatex 路径

    Returns:
        xelatex.exe 的绝对路径

    Raises:
        RuntimeError: 下载或解压失败
        TimeoutError: 下载超时
    """
    xelatex = _get_xelatex_path()
    if os.path.exists(xelatex):
        return xelatex

    # 首次下载+解压
    print(f"[PDF] 首次使用，开始下载 TinyTeX（超时 {TINYTEX_DOWNLOAD_TIMEOUT}s）...")
    _download_and_extract_tinytex(TINYTEX_DOWNLOAD_TIMEOUT)

    assert os.path.exists(xelatex), f"安装完成但 xelatex 不可用: {xelatex}"
    return xelatex


def _download_and_extract_tinytex(timeout: int) -> None:
    """下载 TinyTeX zip 并解压到项目内 setup/tinytex/

    使用线程+Event 实现超时控制。

    Raises:
        TimeoutError: 下载超时
        RuntimeError: 下载或解压失败
    """
    tinytex_dir = _get_tinytex_dir()
    os.makedirs(tinytex_dir, exist_ok=True)

    done = threading.Event()
    error_box = []

    def _download():
        try:
            zip_path = os.path.join(tinytex_dir, "TinyTeX.zip")
            print(f"[PDF] 下载: {TINYTEX_DOWNLOAD_URL}")

            # 用 urllib 下载（支持超时）
            def _report(block_num, block_size, total_size):
                if block_num % 100 == 0 and total_size > 0:
                    pct = block_num * block_size * 100 / total_size
                    print(f"[PDF] 下载进度: {pct:.1f}%", flush=True)

            urllib.request.urlretrieve(TINYTEX_DOWNLOAD_URL, zip_path, reporthook=_report)
            print(f"[PDF] 下载完成，开始解压...")

            # 解压到 tinytex_dir
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tinytex_dir)

            # 删除 zip
            os.unlink(zip_path)
            print(f"[PDF] 解压完成: {tinytex_dir}")

        except Exception as e:
            error_box.append(e)
        finally:
            done.set()

    t = threading.Thread(target=_download, daemon=True)
    t.start()

    if not done.wait(timeout=timeout):
        raise TimeoutError(f"TinyTeX 下载超时（{timeout}s）")

    if error_box:
        raise RuntimeError(f"TinyTeX 下载/解压失败: {error_box[0]}")


def _run_pandoc_to_tex(docx_path: str, tex_path: str, template_path: str) -> None:
    """用 pandoc + 自定义模板将 docx 转为 tex

    Raises:
        RuntimeError: pandoc 不可用或转换失败
    """
    import pypandoc

    try:
        pypandoc.convert_file(
            docx_path,
            "latex",
            outputfile=tex_path,
            extra_args=["--standalone", f"--template={template_path}"],
        )
    except Exception as e:
        raise RuntimeError(f"pandoc 转换 docx→tex 失败: {e}")

    if not os.path.exists(tex_path) or os.path.getsize(tex_path) == 0:
        raise RuntimeError(f"pandoc 转换失败：tex 文件为空或不存在: {tex_path}")


def _run_xelatex(tex_path: str, pdf_path: str) -> None:
    """用 xelatex 编译 tex 为 PDF（运行 2 次以解决交叉引用）

    Raises:
        RuntimeError: 编译失败
    """
    xelatex = _ensure_tinytex()
    xelatex_dir = os.path.dirname(xelatex)

    # 将 xelatex 所在目录加入 PATH，确保子进程能找到相关工具
    os.environ["PATH"] = xelatex_dir + os.pathsep + os.environ.get("PATH", "")

    tex_dir = os.path.dirname(os.path.abspath(tex_path))

    # 运行 2 次（交叉引用）
    for run_idx in range(2):
        cmd = [
            xelatex,
            "-interaction=nonstopmode",
            "-halt-on-error",
            tex_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=tex_dir,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            # 提取错误信息（最后 20 行）
            err_lines = result.stdout.split("\n")[-20:] if result.stdout else []
            err_msg = "\n".join(err_lines) if err_lines else result.stderr
            raise RuntimeError(f"xelatex 编译失败（第{run_idx+1}次）:\n{err_msg}")

    # xelatex 输出 PDF 在 tex 同目录，文件名与 tex 相同但扩展名 .pdf
    generated_pdf = os.path.splitext(tex_path)[0] + ".pdf"
    if not os.path.exists(generated_pdf):
        raise RuntimeError(f"xelatex 编译完成但 PDF 不存在: {generated_pdf}")

    # 复制到目标路径
    if os.path.abspath(generated_pdf) != os.path.abspath(pdf_path):
        shutil.copy(generated_pdf, pdf_path)


def _convert_with_soffice(docx_path: str, pdf_path: str):
    """用 LibreOffice headless 将 docx 转 PDF（支持中文，镜像内已随 Dockerfile 安装）。

    Returns:
        dict 结果（成功/失败），或 None 表示 soffice 不可用，需走原 TinyTeX 链路。
    """
    soffice = shutil.which("soffice")
    if not soffice:
        return None

    tmpdir = tempfile.mkdtemp(prefix="pdf_soffice_")
    try:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, docx_path],
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            detail = (result.stdout or result.stderr or "")[-500:]
            return {"result": None, "error": f"LibreOffice 转换失败: {detail}"}

        generated = os.path.join(
            tmpdir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf"
        )
        if not os.path.exists(generated):
            return {"result": None, "error": f"LibreOffice 转换完成但 PDF 不存在: {generated}"}

        shutil.copy(generated, pdf_path)
        size = os.path.getsize(pdf_path)
        return {
            "result": {
                "message": f"PDF创建成功: {pdf_path}",
                "pdf_path": pdf_path,
                "size": size,
            },
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"result": None, "error": "LibreOffice 转换超时（300s）"}
    except Exception as e:
        return {"result": None, "error": f"LibreOffice 转换异常: {str(e)}"}
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def convert_docx_to_pdf(docx_path: str, pdf_path: str) -> dict:
    """将 docx 转换为 PDF

    Args:
        docx_path: 输入的 docx 文件路径
        pdf_path: 输出的 PDF 文件路径

    Returns:
        成功: {"result": {"message": ..., "pdf_path": ...}, "error": None}
        失败: {"result": None, "error": ...}
    """
    manual_install_hint = (
        "请手动安装：\n"
        "1. 下载 https://github.com/rstudio/tinytex-releases/releases/download/v2026.06/TinyTeX-v2026.06.zip\n"
        f"2. 解压到项目内 setup/tinytex/ 目录（完整路径: {_get_tinytex_dir()}）\n"
        "3. 确保 setup/tinytex/TinyTeX/bin/windows/xelatex.exe 存在\n"
        "4. 重启 agent 服务"
    )

    # 0. 优先使用 LibreOffice（镜像内已安装，支持中文），失败再回退 TinyTeX 链路
    soffice_result = _convert_with_soffice(docx_path, pdf_path)
    if soffice_result is not None:
        return soffice_result

    # 1. 确保 xelatex 可用（首次下载）
    try:
        _ensure_tinytex()
    except TimeoutError as e:
        return {"result": None, "error": f"TinyTeX 自动下载失败: {e}\n{manual_install_hint}"}
    except Exception as e:
        return {"result": None, "error": f"TinyTeX 初始化失败: {e}\n{manual_install_hint}"}

    # 2. pandoc docx → tex
    template_path = _get_template_path()
    tmpdir = tempfile.mkdtemp(prefix="pdf_convert_")
    tex_path = os.path.join(tmpdir, "input.tex")

    try:
        print(f"[PDF] pandoc 转换 docx→tex: {docx_path}")
        _run_pandoc_to_tex(docx_path, tex_path, template_path)

        # 3. xelatex 编译 tex → PDF
        print(f"[PDF] xelatex 编译 tex→pdf: {pdf_path}")
        _run_xelatex(tex_path, pdf_path)

        if not os.path.exists(pdf_path):
            return {"result": None, "error": f"PDF 生成失败：文件不存在: {pdf_path}"}

        size = os.path.getsize(pdf_path)
        print(f"[PDF] 生成成功: {pdf_path} ({size} bytes)")
        return {
            "result": {"message": f"PDF创建成功: {pdf_path}", "pdf_path": pdf_path, "size": size},
            "error": None,
        }

    except Exception as e:
        return {"result": None, "error": f"PDF生成失败: {str(e)}"}

    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
