#!/usr/bin/env bash
# [AIPDD] 启动 VectCut 剪辑服务（独立进程，与主 API 低耦合）。
#
# 用法：
#   scripts/start-vectcut.sh              # 默认 127.0.0.1:9001
#   VECTCUT_PORT=9101 scripts/start-vectcut.sh
#
# 服务代码位于 services/vectcut/，使用服务私有 venv（uv 创建），
# 与主仓库的 .venv 完全隔离；升级 VectCut 上游时本脚本无需改动。
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/.." && pwd)"
service_dir="$root_dir/services/vectcut"

port="${VECTCUT_PORT:-9001}"
host="${VECTCUT_HOST:-127.0.0.1}"

if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
  else
    echo "uv is required. Install it first: https://docs.astral.sh/uv/" >&2
    exit 2
  fi
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Warning: ffmpeg is not on PATH. Media download/transcode will fail." >&2
fi

cd "$service_dir"

if [ "${1:-}" = "--reinstall" ]; then
  echo "Reinstalling VectCut service dependencies ..."
  uv venv .venv --python 3.12
  uv pip install --python .venv/bin/python -r requirements.txt
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating VectCut service venv (uv) and installing dependencies ..."
  uv venv .venv --python 3.12
  uv pip install --python .venv/bin/python -r requirements.txt
fi

# 素材根：本机部署时 Dramaclaw 素材位于仓库 state/ 与 output/。
export VECTCUT_MEDIA_ROOTS="${VECTCUT_MEDIA_ROOTS:-$root_dir/state:$root_dir/output}"
export VECTCUT_HOST="$host"
export VECTCUT_PORT="$port"

echo "Starting VectCut service at http://$host:$port"
echo "Health check: http://127.0.0.1:$port/aipdd/health"
exec .venv/bin/python aipdd_server.py