#!/usr/bin/env bash
# 轻量守护：桥挂了且凭证仍有效时自动 ./start.sh。不处理 -14（需你重新扫码）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p logs state
LOG="$ROOT/logs/keepalive.log"
PID_FILE="$ROOT/state/keepalive.pid"
INTERVAL_S="${KEEPALIVE_INTERVAL_S:-60}"

echo $$ > "$PID_FILE"
chmod 600 "$PID_FILE" 2>/dev/null || true

log() {
  printf '[%s] %s\n' "$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S CST')" "$*" >> "$LOG"
}

bridge_alive() {
  local pid=""
  if [[ -f "$ROOT/state/bridge.pid" ]]; then
    pid="$(cat "$ROOT/state/bridge.pid" 2>/dev/null || true)"
  fi
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  return 1
}

log "keepalive 启动 interval=${INTERVAL_S}s pid=$$"

while true; do
  if [[ -f "$ROOT/state/STALE_TOKEN" ]]; then
    log "检测到 STALE_TOKEN，跳过自动拉起（需重新 bind）"
  elif [[ ! -f "$ROOT/state/credentials.json" ]]; then
    log "无 credentials，跳过"
  elif bridge_alive; then
    :
  else
    log "bridge 未运行，执行 start.sh"
    if "$ROOT/start.sh" >> "$LOG" 2>&1; then
      log "start.sh 完成"
    else
      log "start.sh 失败 exit=$?"
    fi
  fi
  sleep "$INTERVAL_S"
done
