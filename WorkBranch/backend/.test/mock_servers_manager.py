#!/usr/bin/env python3
"""
Mock Servers Manager

同时启动 AI 研判和设施报告的 mock 服务器
"""
import os
import sys
import signal
import subprocess
import threading
import time
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


class MockServerManager:
    def __init__(self):
        self.processes = []
        self.tools_dir = get_project_root() / "service" / "agent_service" / "tools"

    def start_ai_judgment_server(self, host: str = "localhost", port: int = 8080):
        """启动 AI 研判 mock 服务器"""
        script = self.tools_dir / "ai_judgment_mock_server.py"
        if not script.exists():
            print(f"[ERROR] AI Judgment mock server not found: {script}")
            return None

        cmd = [sys.executable, str(script), "--host", host, "--port", str(port)]
        print(f"[AI Judgment] Starting server at {host}:{port}...")

        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }

        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **kwargs)

        def stream_output():
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"[AI-JUDGMENT] {line.rstrip()}")

        thread = threading.Thread(target=stream_output, daemon=True)
        thread.start()

        self.processes.append(("AI Judgment", proc))
        print(f"[AI Judgment] Server started (PID: {proc.pid})")
        return proc

    def start_facility_report_server(self, host: str = "localhost", port: int = 8001):
        """启动设施报告 mock 服务器"""
        script = self.tools_dir / "facility_report_mock_server.py"
        if not script.exists():
            print(f"[ERROR] Facility Report mock server not found: {script}")
            return None

        cmd = [sys.executable, str(script), "--host", host, "--port", str(port)]
        print(f"[Facility Report] Starting server at {host}:{port}...")

        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }

        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **kwargs)

        def stream_output():
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"[FACILITY-REPORT] {line.rstrip()}")

        thread = threading.Thread(target=stream_output, daemon=True)
        thread.start()

        self.processes.append(("Facility Report", proc))
        print(f"[Facility Report] Server started (PID: {proc.pid})")
        return proc

    def start_all(self):
        """启动所有 mock 服务器"""
        print("\n" + "=" * 60)
        print("  Starting Mock Servers")
        print("=" * 60 + "\n")

        self.start_ai_judgment_server()
        time.sleep(0.5)
        self.start_facility_report_server()

        print("\n" + "=" * 60)
        print("  All Mock Servers Started")
        print("=" * 60 + "\n")

        print("Mock Servers:")
        print(f"  - AI Judgment:       http://localhost:8080")
        print(f"  - Facility Report:   http://localhost:8001")
        print("\nPress Ctrl+C to stop all servers\n")

    def stop_all(self):
        """停止所有 mock 服务器"""
        print("\n[Manager] Stopping all mock servers...")

        for name, proc in self.processes:
            if proc.poll() is None:
                print(f"[Manager] Stopping {name} (PID: {proc.pid})...")
                try:
                    if os.name == "nt":
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    print(f"[Manager] Force killing {name}...")
                    proc.kill()
                    proc.wait(timeout=5)
                print(f"[Manager] {name} stopped")

        self.processes.clear()
        print("[Manager] All servers stopped")

    def wait(self):
        """等待所有服务器进程结束"""
        try:
            while True:
                alive = [p for _, p in self.processes if p.poll() is None]
                if not alive:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop_all()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Mock Servers Manager")
    parser.add_argument("--ai-port", type=int, default=8080, help="AI Judgment server port (default: 8080)")
    parser.add_argument("--report-port", type=int, default=8001, help="Facility Report server port (default: 8001)")
    parser.add_argument("--host", default="localhost", help="Host to bind (default: localhost)")
    args = parser.parse_args()

    manager = MockServerManager()

    def signal_handler(sig, frame):
        print("\n[Signal] Received signal, shutting down...")
        manager.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    manager.start_ai_judgment_server(host=args.host, port=args.ai_port)
    time.sleep(0.5)
    manager.start_facility_report_server(host=args.host, port=args.report_port)

    print("\n" + "=" * 60)
    print("  All Mock Servers Running")
    print("=" * 60)
    print(f"\nMock Servers:")
    print(f"  - AI Judgment:     http://{args.host}:{args.ai_port}")
    print(f"  - Facility Report: http://{args.host}:{args.report_port}")
    print("\nPress Ctrl+C to stop\n")

    manager.wait()


if __name__ == "__main__":
    main()
