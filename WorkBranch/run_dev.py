import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


MCP_CONFIG_ENV = "AGENTB_MCP_UPSTREAM_CONFIG"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8181


def print_line(message: str, *, stream: str = "stdout") -> None:
    target = sys.stderr if stream == "stderr" else sys.stdout
    target.buffer.write((message + "\n").encode(target.encoding or "utf-8", errors="replace"))
    target.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start backend dev server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true")
    parser.add_argument(
        "--mcp",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="随后端一起启动上游工具 MCP 服务（默认开启，用 --no-mcp 关闭）",
    )
    parser.add_argument(
        "--mcp-config",
        default=None,
        help=(
            "上游工具 MCP 服务配置文件路径；默认取环境变量 "
            f"{MCP_CONFIG_ENV} 或 WorkBranch/mcp_servers/upstream_tools/config.json"
        ),
    )
    parser.add_argument("--mcp-host", default=DEFAULT_MCP_HOST)
    parser.add_argument("--mcp-port", type=int, default=DEFAULT_MCP_PORT)
    args = parser.parse_args()

    return args


def resolve_python(workspace_root: Path) -> str:
    candidates = [
        workspace_root / ".venv" / "Scripts" / "python.exe",
        workspace_root / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise RuntimeError("Python executable not found.")





def stream_output(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None

    for line in process.stdout:
        print_line(f"[{name}] {line.rstrip()}")


def ensure_backend_runtime(python_executable: str) -> None:
    command = [python_executable, "-c", "import uvicorn"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "uvicorn is not installed in the selected Python environment. Install dependencies with `pip install -r requirements.txt`."
        )


def resolve_mcp_config_path(root_dir: Path, explicit: str | None) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser()
    else:
        env_value = os.getenv(MCP_CONFIG_ENV)
        candidate = (
            Path(env_value).expanduser()
            if env_value
            else root_dir / "mcp_servers" / "upstream_tools" / "config.json"
        )

    if not candidate.is_file():
        raise RuntimeError(
            f"MCP 服务配置文件不存在：{candidate}。"
            f"请准备区域配置（参考 mcp_servers/upstream_tools/config.example.json），"
            f"用 --mcp-config 指定，或设置环境变量 {MCP_CONFIG_ENV}；"
            f"确需只启后端请加 --no-mcp。"
        )

    return candidate


def start_process(
    name: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[str], threading.Thread]:
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if env is not None:
        kwargs["env"] = env

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    thread = threading.Thread(target=stream_output, args=(name, process), daemon=True)
    thread.start()
    return process, thread


def stop_process(name: str, process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    try:
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                process.terminate()
        else:
            process.terminate()

        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print_line(f"[{name}] force killing process")
        process.kill()
        process.wait(timeout=5)


def get_health_check_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def wait_for_http_health(url: str, process: subprocess.Popen[str], timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout

    while time.time() < deadline:
        if process.poll() is not None:
            return False

        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (URLError, TimeoutError):
            pass

        time.sleep(0.5)

    return False


def wait_for_backend_health(host: str, port: int, process: subprocess.Popen[str], timeout: float = 20.0) -> bool:
    health_host = get_health_check_host(host)
    url = f"http://{health_host}:{port}/health"
    return wait_for_http_health(url, process, timeout)


def main() -> int:
    args = parse_args()
    root_dir = Path(__file__).resolve().parent
    workspace_root = root_dir.parent
    backend_dir = root_dir / "backend"

    if not backend_dir.is_dir():
        raise RuntimeError(f"Backend directory not found: {backend_dir}")

    processes: list[tuple[str, subprocess.Popen[str], threading.Thread]] = []
    backend_process: subprocess.Popen[str] | None = None
    mcp_process: subprocess.Popen[str] | None = None

    try:
        python_executable = resolve_python(workspace_root)
        ensure_backend_runtime(python_executable)

        mcp_config_path: Path | None = None
        if args.mcp:
            mcp_config_path = resolve_mcp_config_path(root_dir, args.mcp_config)
            mcp_env = dict(os.environ)
            mcp_env[MCP_CONFIG_ENV] = str(mcp_config_path)
            mcp_env["AGENTB_MCP_HOST"] = args.mcp_host
            mcp_env["AGENTB_MCP_PORT"] = str(args.mcp_port)
            mcp_command = [
                python_executable,
                "-m",
                "mcp_servers.upstream_tools.server",
            ]
            print_line(f"[launcher] starting upstream tools mcp service in {root_dir}")
            print_line(f"[launcher] mcp config: {mcp_config_path}")
            mcp_process, mcp_thread = start_process("mcp", mcp_command, root_dir, env=mcp_env)
            processes.append(("mcp", mcp_process, mcp_thread))

        backend_command = [
            python_executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            args.host,
            "--port",
            str(args.backend_port),
        ]
        if not args.no_reload:
            backend_command.append("--reload")

        print_line(f"[launcher] starting backend in {backend_dir}")
        backend_process, backend_thread = start_process("backend", backend_command, backend_dir)
        processes.append(("backend", backend_process, backend_thread))

        if not processes:
            print_line("[launcher] nothing to start")
            return 0

        print_line(f"[launcher] backend expected at http://{args.host}:{args.backend_port}")

        if mcp_process is not None:
            mcp_health_host = get_health_check_host(args.mcp_host)
            mcp_health_url = f"http://{mcp_health_host}:{args.mcp_port}/healthz"
            print_line(f"[launcher] checking mcp health at {mcp_health_url}")
            if wait_for_http_health(mcp_health_url, mcp_process, timeout=30.0):
                print_line(f"[launcher] mcp health check passed at {mcp_health_url}")
            else:
                print_line(
                    f"[launcher] mcp health check did not pass within timeout at {mcp_health_url}"
                )

        if backend_process is not None:
            health_host = get_health_check_host(args.host)
            print_line(f"[launcher] checking backend health at http://{health_host}:{args.backend_port}/health")
            if wait_for_backend_health(args.host, args.backend_port, backend_process):
                print_line(f"[launcher] backend health check passed at http://{health_host}:{args.backend_port}/health")
            else:
                print_line(f"[launcher] backend health check did not pass within timeout at http://{health_host}:{args.backend_port}/health")

        print_line("[launcher] press Ctrl+C to stop all services")

        while True:
            for name, process, _ in processes:
                return_code = process.poll()
                if return_code is not None:
                    if return_code == 0:
                        print_line(f"[launcher] {name} exited")
                    else:
                        print_line(f"[launcher] {name} exited with code {return_code}")
                    return return_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print_line("[launcher] stopping services")
        return 0
    finally:
        for name, process, _ in reversed(processes):
            stop_process(name, process)

        for _, _, thread in processes:
            thread.join(timeout=1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print_line(f"[launcher] {error}", stream="stderr")
        raise SystemExit(1)

