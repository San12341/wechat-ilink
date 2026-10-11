#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/state/bridge.pid"
if [[ ! -f "$PID_FILE" ]]; then
  echo "未找到 pid 文件，bridge 似乎未在运行"
  exit 0
fi
PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "${PID:-}" ]]; then
  rm -f "$PID_FILE"
  echo "pid 文件为空，已清理"
  exit 0
fi
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null || true
  for i in 1 2 3 4 5; do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.4
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID" 2>/dev/null || true
  fi
  echo "已停止 bridge pid=$PID"
else
  echo "进程 $PID 已不存在"
fi
rm -f "$PID_FILE"
