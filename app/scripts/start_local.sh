#!/bin/bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null || { echo '缺少Python 3.11+；不会自动安装。'; exit 2; }
"$PYTHON_BIN" scripts/preflight.py || { echo '请先处理缺失的Python/Node或被占用的8765端口；不会停止其他程序，不需要Git。'; exit 2; }
if [[ -n "${CAREOPS_VENV_PYTHON:-}" ]]; then
  [[ -x "$CAREOPS_VENV_PYTHON" ]] || { echo "指定的已有虚拟环境Python不可执行"; exit 2; }
  PY="$CAREOPS_VENV_PYTHON"
else
if [[ ! -x .venv/bin/python ]]; then
  echo '首次启动：仅在此候选包内创建Python虚拟环境并安装requirements.txt依赖。'
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi
PY=.venv/bin/python
fi
# Validate existing environment instead of silently reusing a partial installation.
"$PY" -c 'import fastapi, uvicorn, sqlalchemy, argon2, pydantic' || { echo '虚拟环境依赖未安装完整；请执行 .venv/bin/python -m pip install -r requirements.txt'; exit 2; }
if [[ "$(uname -s)" == Darwin ]]; then
  export CAREOPS_DATA_DIR="${CAREOPS_DATA_DIR:-$HOME/Library/Application Support/CareOps-v04-preview}"
else
  export CAREOPS_DATA_DIR="${CAREOPS_DATA_DIR:-$HOME/.local/share/CareOps-v04-preview}"
fi
export CAREOPS_ORIGIN="${CAREOPS_ORIGIN:-http://127.0.0.1:8765}"
# Preserve old M1/M2 business data and accounts. Explicit snapshot-backed M3 migration first.
if [[ -n "${CAREOPS_DATABASE_URL:-}" ]]; then
  echo '此本机启动脚本只使用CAREOPS_DATA_DIR中的SQLite。检测到自定义数据库URL，停止，避免备份错误数据库。'; exit 2
fi
"$PY" scripts/migrate_m3.py --source "$CAREOPS_DATA_DIR/careops.sqlite3"
# Bind/listen only on localhost; do not tunnel or change server firewall rules.
"$PY" -m careops.cli init --demo
"$PY" -m careops.cli check
printf '\n打开 http://127.0.0.1:8765\n账户文件：%s/INITIAL_CREDENTIALS_DEMO-01.txt\n按 Ctrl+C 停止服务，已提交的数据不会被删除。\n\n' "$CAREOPS_DATA_DIR"
exec "$PY" -m uvicorn careops.app:create_app --factory --host 127.0.0.1 --port 8765 --no-access-log --no-server-header
