"""
Gunicorn 配置文件

启动命令:
    gunicorn app:app -c gunicorn.conf.py

开发模式:
    gunicorn app:app -c gunicorn.conf.py --reload
"""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))

worker_class = "uvicorn.workers.UvicornWorker"

keepalive = 120

timeout = 120

graceful_timeout = 30

max_requests = 1000

max_requests_jitter = 50

preload_app = False

reload = os.environ.get("GUNICORN_RELOAD", "false").lower() == "true"

accesslog = "-"

errorlog = "-"

loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

capture_output = True

forwarded_allow_ips = "*"

proxy_allow_ips = "*


def on_starting(server):
    server.log.info("Gunicorn server starting...")


def on_exit(server):
    server.log.info("Gunicorn server shutting down...")


def when_ready(server):
    server.log.info(f"Gunicorn server ready. Listening on: {bind}")
    server.log.info(f"Workers: {workers}, Worker class: {worker_class}")
