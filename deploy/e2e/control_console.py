#!/usr/bin/env python3
"""Local control console for long-running AgentB Compose and E2E operations."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
HTML_PATH = Path(__file__).with_name("control_console.html")
COMPOSE_FILES = ("compose.yml", "compose.standalone.yml")
MAX_LOG_LINES = 4_000


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def compose_command(*arguments: str) -> list[str]:
    command = ["wsl.exe", "--cd", str(REPO_ROOT), "docker", "compose"]
    env_file = REPO_ROOT / ".env.compose"
    if env_file.is_file():
        command.extend(("--env-file", ".env.compose"))
    for compose_file in COMPOSE_FILES:
        command.extend(("-f", compose_file))
    command.extend(arguments)
    return command


def regression_command() -> list[str]:
    return [
        "cmd.exe",
        "/d",
        "/c",
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "deploy\\e2e\\run_regression.ps1",
    ]


ACTIONS: dict[str, Callable[[], list[str]]] = {
    "compose_up": lambda: compose_command("up", "-d"),
    "compose_build": lambda: compose_command("up", "-d", "--build"),
    "compose_stop": lambda: compose_command("stop"),
    "compose_logs": lambda: compose_command("logs", "--no-color", "--tail", "300"),
    "regression": regression_command,
}


@dataclass
class Job:
    id: int
    action: str
    status: str = "queued"
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    lines: list[str] = field(default_factory=list)

    def append(self, line: str) -> None:
        self.lines.append(line.rstrip("\r\n"))
        if len(self.lines) > MAX_LOG_LINES:
            del self.lines[: len(self.lines) - MAX_LOG_LINES]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "log": "\n".join(self.lines),
        }


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._jobs: dict[int, Job] = {}
        self._active_job_id: int | None = None

    def start(self, action: str) -> Job:
        if action not in ACTIONS:
            raise KeyError(action)
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if active.status in {"queued", "running"}:
                    raise RuntimeError(
                        f"{active.action} is already running as job {active.id}"
                    )
            job = Job(self._next_id, action)
            self._next_id += 1
            self._jobs[job.id] = job
            self._active_job_id = job.id
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        command = ACTIONS[job.action]()
        with self._lock:
            job.status = "running"
            job.started_at = time.time()
            job.append(f"$ {' '.join(command)}")
        try:
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_creation_flags(),
            )
            assert process.stdout is not None
            for line in process.stdout:
                with self._lock:
                    job.append(line)
            exit_code = process.wait()
            with self._lock:
                job.exit_code = exit_code
                job.status = "succeeded" if exit_code == 0 else "failed"
        except Exception as exc:
            with self._lock:
                job.exit_code = -1
                job.status = "failed"
                job.append(f"control console error: {exc}")
        finally:
            with self._lock:
                job.finished_at = time.time()
                if self._active_job_id == job.id:
                    self._active_job_id = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            jobs = [self._jobs[key].as_dict() for key in sorted(self._jobs)[-10:]]
            active = self._jobs.get(self._active_job_id)
            return {
                "active_job": active.as_dict() if active else None,
                "jobs": jobs,
            }

    def has_active_job(self) -> bool:
        with self._lock:
            active = self._jobs.get(self._active_job_id)
            return active is not None and active.status in {"queued", "running"}


class KeepaliveManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._started_at: float | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.snapshot()
            self._process = subprocess.Popen(
                [
                    "wsl.exe",
                    "sh",
                    "-lc",
                    "exec -a agentb-console-keepalive sleep 21600",
                ],
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
            self._started_at = time.time()
            return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            self._process = None
            self._started_at = None
        try:
            subprocess.run(
                ["wsl.exe", "pkill", "-f", "^agentb-console-keepalive"],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                creationflags=_creation_flags(),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process is not None and process.poll() is None:
            process.terminate()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {"running": running, "started_at": self._started_at if running else None}


def _run_status_command(timeout: float = 20.0) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            compose_command("ps", "--format", "json"),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_creation_flags(),
        )
        return completed.returncode, completed.stdout or completed.stderr
    except subprocess.TimeoutExpired:
        return 124, f"Compose status timed out after {timeout:.0f}s"
    except OSError as exc:
        return -1, str(exc)


def compose_status() -> dict[str, Any]:
    exit_code, output = _run_status_command()
    services: list[dict[str, Any]] = []
    if exit_code == 0:
        for raw_line in output.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            services.append(
                {
                    "name": item.get("Name") or item.get("Service"),
                    "service": item.get("Service"),
                    "state": item.get("State"),
                    "health": item.get("Health"),
                    "status": item.get("Status"),
                }
            )
    return {"exit_code": exit_code, "error": None if exit_code == 0 else output, "services": services}


def endpoint_health(url: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with urlopen(url, timeout=2.0) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            return {
                "ok": response.status == 200,
                "status": response.status,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "body": body,
            }
    except (OSError, URLError) as exc:
        return {
            "ok": False,
            "status": None,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "body": str(exc),
        }


class ConsoleState:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(24)
        self.jobs = JobManager()
        self.keepalive = KeepaliveManager()


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "AgentBControlConsole/1.0"

    @property
    def state(self) -> ConsoleState:
        return self.server.console_state  # type: ignore[attr-defined]

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        body = HTML_PATH.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = self.headers.get("X-Control-Token")
        origin = self.headers.get("Origin")
        expected_origin = f"http://{self.headers.get('Host')}"
        return secrets.compare_digest(token or "", self.state.token) and (
            origin is None or origin == expected_origin
        )

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_html()
            return
        if path == "/api/bootstrap":
            self._send_json(
                {
                    "token": self.state.token,
                    "repo": str(REPO_ROOT),
                    "actions": list(ACTIONS),
                }
            )
            return
        if path == "/api/state":
            self._send_json(
                {
                    "compose": compose_status(),
                    "router": endpoint_health("http://127.0.0.1:8152/router-health"),
                    "api": endpoint_health("http://127.0.0.1:8152/api/health"),
                    "keepalive": self.state.keepalive.snapshot(),
                    **self.state.jobs.snapshot(),
                }
            )
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/actions/"):
            action = path.rsplit("/", 1)[-1]
            try:
                job = self.state.jobs.start(action)
            except KeyError:
                self._send_json({"error": "unknown action"}, HTTPStatus.NOT_FOUND)
                return
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
                return
            self._send_json(job.as_dict(), HTTPStatus.ACCEPTED)
            return
        if path == "/api/keepalive/start":
            self._send_json(self.state.keepalive.start(), HTTPStatus.ACCEPTED)
            return
        if path == "/api/keepalive/stop":
            if self.state.jobs.has_active_job():
                self._send_json(
                    {"error": "cannot stop WSL keepalive while a job is active"},
                    HTTPStatus.CONFLICT,
                )
                return
            self._send_json(self.state.keepalive.stop())
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


def find_port(host: str, preferred: int, attempts: int = 10) -> int:
    for port in range(preferred, preferred + attempts):
        try:
            probe = ThreadingHTTPServer((host, port), ConsoleHandler)
        except OSError:
            continue
        probe.server_close()
        return port
    raise OSError(f"No available port in {preferred}-{preferred + attempts - 1}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8153)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("The control console may only bind to localhost")
    port = find_port("127.0.0.1", args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), ConsoleHandler)
    server.console_state = ConsoleState()  # type: ignore[attr-defined]
    print(f"AgentB control console: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
