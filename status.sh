#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/state/bridge.pid"
echo "=== wechat-ilink status ==="
if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "bridge: 运行中 pid=$PID"
  else
    echo "bridge: 未运行（pid 文件陈旧: ${PID:-空}）"
  fi
else
  echo "bridge: 未运行"
fi
if [[ -f "$ROOT/state/STALE_TOKEN" ]]; then
  echo "警告: 存在 STALE_TOKEN（凭证失效，请重新 bind.py）"
  head -n 2 "$ROOT/state/STALE_TOKEN" 2>/dev/null || true
else
  echo "STALE_TOKEN: 无"
fi
if [[ -f "$ROOT/state/credentials.json" ]]; then
  echo "credentials: 已存在"
else
  echo "credentials: 缺失（需 bind）"
fi
KPID_FILE="$ROOT/state/keepalive.pid"
if [[ -f "$KPID_FILE" ]]; then
  KPID="$(cat "$KPID_FILE" 2>/dev/null || true)"
  if [[ -n "${KPID:-}" ]] && kill -0 "$KPID" 2>/dev/null; then
    echo "keepalive: 运行中 pid=$KPID"
  else
    echo "keepalive: 未运行（pid 文件陈旧）"
  fi
else
  echo "keepalive: 未运行"
fi
if [[ -f "$ROOT/state/webhook.env" ]]; then
  echo "webhook.env: 已存在"
else
  echo "webhook.env: 缺失"
fi
echo "--- 最近日志 ---"
if [[ -f "$ROOT/logs/bridge.log" ]]; then
  tail -n 15 "$ROOT/logs/bridge.log"
else
  echo "(尚无 logs/bridge.log)"
fi
