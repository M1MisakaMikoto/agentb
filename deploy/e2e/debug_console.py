#!/usr/bin/env python3
"""Local-only control plane for long-running AgentB Docker/E2E diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = Path(__file__).resolve().parent
COMPOSE = (
    "docker",
    "compose",
    "-f",
    "compose.yml",
    "-f",
    "compose.standalone.yml",
)
MAX_LOG_LINES = 4000


def windows_repo_root() -> str:
    completed = subprocess.run(
        ["wslpath", "-w", str(REPO_ROOT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def action_command(action: str) -> list[str]:
    compose_actions = {
        "start-deps": [*COMPOSE, "up", "-d", "mysql", "redis"],
        "start-agentb-1": [*COMPOSE, "up", "-d", "agentb-1"],
        "start-agentb-2": [*COMPOSE, "up", "-d", "agentb-2"],
        "start-agentb-3": [*COMPOSE, "up", "-d", "agentb-3"],
        "start-rag": [*COMPOSE, "up", "-d", "agentb-rag-worker"],
        "start-router": [*COMPOSE, "up", "-d", "agentb-router"],
        "start-stack": [*COMPOSE, "up", "-d"],
        "build-start": [*COMPOSE, "up", "-d", "--build"],
        "stop-stack": [*COMPOSE, "stop"],
        "compose-logs": [*COMPOSE, "logs", "--no-color", "--tail", "500"],
    }
    if action in compose_actions:
        return compose_actions[action]
    if action == "regression":
        script = Path(windows_repo_root()) / "deploy" / "e2e" / "run_regression.ps1"
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    raise KeyError(action)


@dataclass
class Job:
    job_id: str
    action: str
    command: list[str]
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    cancelled: bool = False
    lines: deque[str] = field(
        default_factory=lambda: deque(maxlen=MAX_LOG_LINES),
        repr=False,
    )
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def snapshot(self) -> dict:
        payload = asdict(self)
        payload.pop("process", None)
        payload["lines"] = list(self.lines)
        payload["running"] = self.finished_at is None
        return payload


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Job | None = None

    def snapshot(self) -> dict | None:
        with self._lock:
            return self._current.snapshot() if self._current else None

    def start(self, action: str) -> Job:
        command = action_command(action)
        with self._lock:
            if self._current and self._current.finished_at is None:
                raise RuntimeError(
                    f"Job {self._current.job_id} ({self._current.action}) is still running"
                )
            job = Job(secrets.token_hex(6), action, command)
            self._current = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _append(self, job: Job, line: str) -> None:
        with self._lock:
            job.lines.append(line.rstrip("\r\n"))

    def _run(self, job: Job) -> None:
        self._append(job, f"$ {' '.join(job.command)}")
        try:
            process = subprocess.Popen(
                job.command,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
            with self._lock:
                job.process = process
            assert process.stdout is not None
            for line in process.stdout:
                self._append(job, line)
            exit_code = process.wait()
        except Exception as exc:
            self._append(job, f"ERROR: {exc}")
            exit_code = -1
        with self._lock:
            job.exit_code = exit_code
            job.finished_at = time.time()
            job.process = None

    def cancel(self) -> Job:
        with self._lock:
            job = self._current
            if not job or job.finished_at is not None or not job.process:
                raise RuntimeError("No running job")
            job.cancelled = True
            process = job.process
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        return job


def compose_status() -> dict:
    started = time.time()
    try:
        completed = subprocess.run(
            [*COMPOSE, "ps", "--format", "json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "Compose status timed out after 45 seconds",
            "services": [],
            "duration": time.time() - started,
        }

    services = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            services.append(json.loads(line))
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": f"Unexpected Compose output: {line[:300]}",
                "services": services,
                "duration": time.time() - started,
            }
    return {
        "ok": completed.returncode == 0,
        "error": completed.stderr.strip() or None,
        "services": services,
        "duration": time.time() - started,
    }


class ConsoleHandler(BaseHTTPRequestHandler):
    manager: JobManager
    token: str

    def log_message(self, format: str, *args) -> None:
        print(f"[console] {self.address_string()} {format % args}")

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _asset(self, name: str, content_type: str) -> None:
        body = (ASSET_ROOT / name).read_bytes()
        if name.endswith(".html"):
            body = body.replace(b"__DEBUG_TOKEN__", self.token.encode("ascii"))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Debug-Token", ""),
            self.token,
        )

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._asset("debug_console.html", "text/html; charset=utf-8")
        elif path == "/debug_console.js":
            self._asset("debug_console.js", "text/javascript; charset=utf-8")
        elif path == "/api/health":
            self._json({"ok": True})
        elif not self._authorized():
            self._json({"error": "unauthorized"}, HTTPStatus.FORBIDDEN)
        elif path == "/api/status":
            self._json(compose_status())
        elif path == "/api/job":
            self._json({"job": self.manager.snapshot()})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authorized():
            self._json({"error": "unauthorized"}, HTTPStatus.FORBIDDEN)
            return
        try:
            if path.startswith("/api/actions/"):
                action = path.removeprefix("/api/actions/")
                self._json({"job": self.manager.start(action).snapshot()}, HTTPStatus.ACCEPTED)
            elif path == "/api/job/cancel":
                self._json({"job": self.manager.cancel().snapshot()}, HTTPStatus.ACCEPTED)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except KeyError:
            self._json({"error": "unknown action"}, HTTPStatus.NOT_FOUND)
        except RuntimeError as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8153)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = secrets.token_urlsafe(24)
    handler = type(
        "BoundConsoleHandler",
        (ConsoleHandler,),
        {"manager": JobManager(), "token": token},
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"AgentB debug console: http://{args.host}:{args.port}/", flush=True)
    print("The page token is injected at request time; keep this process local.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
