"""PDF 转换器：docx → tex → xelatex → PDF

转换链：
1. 已有 docx（由 document_tools._docx_write 生成）
2. pandoc 用自定义最简模板将 docx 转为 tex（绕过默认模板的 unicode-math 依赖）
3. pytinytex 调 xelatex 编译 tex 为 PDF

首次使用会自动下载 TinyTeX（约 244MB），超时 600s 后抛错含手动安装说明。
"""
import os
import sys
import threading
import tempfile

TINYTEX_DOWNLOAD_TIMEOUT = 600  # 秒


def _get_template_path() -> str:
    """返回 simple_template.tex 的绝对路径（与本文件同目录）"""
    return os.path.join(os.path.dirname(__file__), "simple_template.tex")


def _get_font_path() -> str:
    """获取系统 SimHei 字体路径（动态适配系统盘）"""
    system_root = os.environ.get("SystemRoot", r"C:\WINDOWS")
    return system_root.replace("\\", "/") + "/Fonts/simhei.ttf"


def _ensure_tinytex() -> str:
    """确保 TinyTeX 已安装，返回 xelatex 可执行文件路径"""
    import pytinytex

    # 已安装则直接返回
    try:
        engine = pytinytex.get_xelatex_engine()
        if engine and os.path.exists(engine):
            return engine
    except Exception:
        pass

    # 首次下载（带超时）
    print(f"[PDF] 首次使用，开始下载 TinyTeX（超时 {TINYTEX_DOWNLOAD_TIMEOUT}s）...")
    _download_tinytex_with_timeout(TINYTEX_DOWNLOAD_TIMEOUT)

    engine = pytinytex.get_xelatex_engine()
    assert engine and os.path.exists(engine), f"下载完成但 xelatex 不可用: {engine}"
    return engine


def _download_tinytex_with_timeout(timeout: int) -> None:
    """首次下载 TinyTeX，超时抛错含手动安装说明

    pytinytex 内部用 urlopen 无超时控制，这里用线程+Event 实现。
    """
    import pytinytex

    done = threading.Event()
    error_box = []

    def _download():
        try:
            # variation=2 是 Windows 完整版（2026.06 起 variation 0/1 无 Windows 包）
            pytinytex.download_tinytex(variation=2)
        except Exception as e:
            error_box.append(e)
        finally:
            done.set()

    t = threading.Thread(target=_download, daemon=True)
    t.start()

    if not done.wait(timeout=timeout):
        raise TimeoutError(f"TinyTeX 下载超时（{timeout}s）")

    if error_box:
        raise error_box[0]


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
    """用 pytinytex 调 xelatex 编译 tex 为 PDF

    Raises:
        RuntimeError: 编译失败
    """
    import pytinytex

    # 将 xelatex 所在目录加入 PATH，确保子进程能找到相关工具
    engine = pytinytex.get_xelatex_engine()
    xelatex_dir = os.path.dirname(engine)
    os.environ["PATH"] = xelatex_dir + os.pathsep + os.environ.get("PATH", "")

    result = pytinytex.compile(tex_path, engine="xelatex", auto_install=True)
    if not result.success:
        err_msgs = [e.message for e in result.errors[:3]]
        raise RuntimeError(f"xelatex 编译失败: {'; '.join(err_msgs)}")

    if not os.path.exists(result.pdf_path):
        raise RuntimeError(f"xelatex 编译完成但 PDF 不存在: {result.pdf_path}")

    # 复制到目标路径（pytinytex 输出在 tex 同目录）
    if os.path.abspath(result.pdf_path) != os.path.abspath(pdf_path):
        import shutil
        shutil.copy(result.pdf_path, pdf_path)


def convert_docx_to_pdf(docx_path: str, pdf_path: str) -> dict:
    """将 docx 转换为 PDF

    Args:
        docx_path: 输入的 docx 文件路径
        pdf_path: 输出的 PDF 文件路径

    Returns:
        成功: {"result": {"message": ..., "pdf_path": ...}, "error": None}
        失败: {"result": None, "error": ...}
    """
    # 1. 确保 xelatex 可用（首次下载）
    try:
        _ensure_tinytex()
    except TimeoutError as e:
        return {
            "result": None,
            "error": (
                f"TinyTeX 自动下载失败: {e}\n"
                "请手动安装：\n"
                "1. 下载 https://github.com/rstudio/tinytex-releases/releases/download/v2026.06/TinyTeX-v2026.06.zip\n"
                "2. 解压到用户目录下的 .pytinytex 目录（如 X:\\Users\\<用户名>\\.pytinytex）\n"
                "3. 确保 .pytinytex\\TinyTeX\\bin\\windows\\xelatex.exe 存在\n"
                "4. 重启 agent 服务"
            ),
        }
    except Exception as e:
        return {
            "result": None,
            "error": (
                f"TinyTeX 初始化失败: {e}\n"
                "请手动安装：\n"
                "1. 下载 https://github.com/rstudio/tinytex-releases/releases/download/v2026.06/TinyTeX-v2026.06.zip\n"
                "2. 解压到用户目录下的 .pytinytex 目录（如 X:\\Users\\<用户名>\\.pytinytex）\n"
                "3. 确保 .pytinytex\\TinyTeX\\bin\\windows\\xelatex.exe 存在\n"
                "4. 重启 agent 服务"
            ),
        }

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
        # 清理临时目录
        import shutil
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
