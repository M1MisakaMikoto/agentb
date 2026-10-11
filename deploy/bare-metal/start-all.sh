#!/usr/bin/env bash
# AgentB 裸机（无 Docker）统一启动脚本：上游工具 MCP 服务 + RAG worker + API。
#
# 用法（仓库根目录执行）：
#   AGENTB_MCP_UPSTREAM_CONFIG=/etc/agentb/mcp_upstream_tools.json bash deploy/bare-metal/start-all.sh
#
# 前置条件：Python 虚拟环境已安装 requirements.txt 与 gunicorn/psutil；MySQL/Redis 已就绪；
#          LibreOffice 已安装（.doc 读取与 PDF 生成）；MCP 配置文件已按区域替换。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PYTHON="${AGENTB_VENV_PYTHON:-$REPO_ROOT/.venv/bin/python}"
MCP_CONFIG="${AGENTB_MCP_UPSTREAM_CONFIG:-$REPO_ROOT/WorkBranch/mcp_servers/upstream_tools/config.json}"

fail() {
  echo "[start-all] $*" >&2
  exit 1
}

[ -x "$VENV_PYTHON" ] || fail "Python 虚拟环境不可用：$VENV_PYTHON（可用 AGENTB_VENV_PYTHON 指定）"
[ -f "$MCP_CONFIG" ] || fail "MCP 服务配置文件不存在：$MCP_CONFIG（参考 WorkBranch/mcp_servers/upstream_tools/config.example.json）"
"$VENV_PYTHON" -c "import gunicorn" >/dev/null 2>&1 || fail "gunicorn 未安装：pip install gunicorn psutil"

export AGENTB_MCP_UPSTREAM_CONFIG="$MCP_CONFIG"
export AGENTB_MCP_HOST="${AGENTB_MCP_HOST:-0.0.0.0}"
export AGENTB_MCP_PORT="${AGENTB_MCP_PORT:-8181}"
export AGENTB_MCP_UPSTREAM_TOOLS_URL="${AGENTB_MCP_UPSTREAM_TOOLS_URL:-http://127.0.0.1:${AGENTB_MCP_PORT}/mcp}"

PIDS=()
NAMES=()

start() {
  local name="$1"
  local cwd="$2"
  shift 2
  echo "[start-all] starting ${name}: $*"
  ( cd "$cwd" && exec "$@" ) &
  PIDS+=("$!")
  NAMES+=("$name")
}

shutdown() {
  local index
  echo "[start-all] stopping services"
  for index in "${!PIDS[@]}"; do
    echo "[start-all] stopping ${NAMES[$index]} (pid=${PIDS[$index]})"
    kill "${PIDS[$index]}" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap shutdown INT TERM EXIT

start "mcp" "$REPO_ROOT/WorkBranch" "$VENV_PYTHON" -m mcp_servers.upstream_tools.server
start "rag-worker" "$REPO_ROOT/WorkBranch" "$VENV_PYTHON" -m rag.worker
start "api" "$REPO_ROOT/WorkBranch/backend" "$VENV_PYTHON" -m gunicorn app:app -c gunicorn.conf.py

echo "[start-all] mcp config: ${AGENTB_MCP_UPSTREAM_CONFIG}"
echo "[start-all] mcp endpoint: ${AGENTB_MCP_HOST}:${AGENTB_MCP_PORT}/mcp (health: /healthz)"
echo "[start-all] api bind: ${GUNICORN_BIND:-0.0.0.0:8000}；Ctrl+C 停止全部进程"

status=0
wait -n || status=$?
echo "[start-all] a service exited (status=${status}), stopping the rest"
exit "$status"
