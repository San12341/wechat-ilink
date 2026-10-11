#!/usr/bin/env bash
# 启动 / 重启 bridge 长轮询
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p state logs
chmod 700 state 2>/dev/null || true

PYTHON="$ROOT/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

PID_FILE="$ROOT/state/bridge.pid"
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "检测到已在运行 (pid=$OLD_PID)，先停止…"
    kill "$OLD_PID" 2>/dev/null || true
    for i in 1 2 3 4 5; do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 0.4
    done
    if kill -0 "$OLD_PID" 2>/dev/null; then
      kill -9 "$OLD_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
fi

if [[ ! -f "$ROOT/state/credentials.json" ]]; then
  echo "尚未绑定。请先运行: $PYTHON bind.py" >&2
  exit 1
fi

nohup "$PYTHON" -u "$ROOT/bridge.py" >> "$ROOT/logs/bridge.nohup.log" 2>&1 &
echo $! > "$PID_FILE"
chmod 600 "$PID_FILE" 2>/dev/null || true
echo "已启动 bridge pid=$(cat "$PID_FILE")"
