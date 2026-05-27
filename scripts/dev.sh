#!/usr/bin/env bash
# dev.sh — 一键开发启动 (postgres / backend / frontend static server / preview)
# 用法:
#   ./scripts/dev.sh start      # 启动全部
#   ./scripts/dev.sh stop       # 停掉
#   ./scripts/dev.sh restart    # 重启
#   ./scripts/dev.sh status     # 看状态
#   ./scripts/dev.sh logs       # tail 后端日志

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RPG_DIR="$ROOT/rpg"
FRONTEND_DIR="$ROOT/frontend"
LOG_DIR="$ROOT/.dev-logs"
mkdir -p "$LOG_DIR"

BACKEND_PORT=7860
FRONTEND_PORT=5173
PG_PORT=5432

BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

# ── helpers ────────────────────────────────────────────────────────
_pid_on_port() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1
}

_kill_on_port() {
  local pid; pid="$(_pid_on_port "$1")"
  [ -n "$pid" ] || return 0
  echo "  · 杀掉旧进程 :$1 (pid=$pid)"
  kill -9 "$pid" 2>/dev/null
  sleep 1
}

_color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
_ok()    { _color "32" "✓"; }
_bad()   { _color "31" "✗"; }
_warn()  { _color "33" "!"; }

# ── 健康检查 ───────────────────────────────────────────────────────
check_postgres() {
  local pid; pid="$(_pid_on_port "$PG_PORT")"
  if [ -n "$pid" ]; then
    echo "  $(_ok) Postgres :$PG_PORT (pid=$pid)"
    return 0
  fi
  echo "  $(_bad) Postgres :$PG_PORT 未运行 — 请先启动 Postgres (brew services start postgresql)"
  return 1
}

check_backend() {
  local pid; pid="$(_pid_on_port "$BACKEND_PORT")"
  [ -z "$pid" ] && { echo "  $(_bad) backend :$BACKEND_PORT 未运行"; return 1; }
  local code; code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$BACKEND_PORT/" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "  $(_ok) backend :$BACKEND_PORT (pid=$pid, HTTP $code)"
  else
    echo "  $(_warn) backend :$BACKEND_PORT pid=$pid 但 HTTP=$code (启动中?)"
  fi
}

check_frontend() {
  local pid; pid="$(_pid_on_port "$FRONTEND_PORT")"
  [ -z "$pid" ] && { echo "  $(_bad) frontend :$FRONTEND_PORT 未运行"; return 1; }
  local code; code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$FRONTEND_PORT/Platform.html" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "  $(_ok) frontend :$FRONTEND_PORT (pid=$pid, Platform.html HTTP $code)"
  else
    echo "  $(_warn) frontend :$FRONTEND_PORT pid=$pid 但 HTTP=$code"
  fi
}

# ── 启动 ───────────────────────────────────────────────────────────
start_backend() {
  if [ -n "$(_pid_on_port $BACKEND_PORT)" ]; then
    echo "  $(_warn) backend :$BACKEND_PORT 已运行 — 跳过 (用 restart 强重启)"
    return 0
  fi
  echo "  · 启动 backend → $BACKEND_LOG"
  (
    cd "$RPG_DIR"
    nohup .venv/bin/python app.py > "$BACKEND_LOG" 2>&1 &
    echo "$!" > "$LOG_DIR/backend.pid"
  )
  # 等到 200 或 12s timeout
  local i; for i in $(seq 1 24); do
    sleep 0.5
    local code; code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$BACKEND_PORT/" 2>/dev/null)
    [ "$code" = "200" ] && { echo "  $(_ok) backend ready in ~${i}*0.5s"; return 0; }
  done
  echo "  $(_bad) backend 12s 内没起来,看 $BACKEND_LOG"
  tail -10 "$BACKEND_LOG" | sed 's/^/    /'
  return 1
}

start_frontend() {
  if [ -n "$(_pid_on_port $FRONTEND_PORT)" ]; then
    echo "  $(_warn) frontend :$FRONTEND_PORT 已运行 — 跳过 (用 restart 强重启)"
    return 0
  fi
  echo "  · 启动 frontend static server → $FRONTEND_LOG"
  (
    cd "$FRONTEND_DIR"
    nohup python3 -m http.server "$FRONTEND_PORT" --bind 127.0.0.1 > "$FRONTEND_LOG" 2>&1 &
    echo "$!" > "$LOG_DIR/frontend.pid"
  )
  local i; for i in $(seq 1 12); do
    sleep 0.5
    local code; code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$FRONTEND_PORT/Platform.html" 2>/dev/null)
    [ "$code" = "200" ] && { echo "  $(_ok) frontend ready in ~${i}*0.5s"; return 0; }
  done
  echo "  $(_bad) frontend 6s 内没起来,看 $FRONTEND_LOG"
  return 1
}

# ── 命令分发 ───────────────────────────────────────────────────────
cmd_status() {
  echo "─── dev status ───"
  check_postgres
  check_backend || true
  check_frontend || true
  echo ""
  echo "  日志: $LOG_DIR/{backend.log, frontend.log}"
  echo "  入口: http://127.0.0.1:$FRONTEND_PORT/Platform.html"
}

cmd_start() {
  echo "─── 启动 dev 环境 ───"
  check_postgres || { echo "$(_bad) Postgres 没起,先解决。"; exit 1; }
  start_backend  || exit 1
  start_frontend || exit 1
  echo ""
  echo "$(_ok) 全部就绪 →  http://127.0.0.1:$FRONTEND_PORT/Platform.html"
}

cmd_stop() {
  echo "─── 停 dev 环境 ───"
  _kill_on_port $BACKEND_PORT
  _kill_on_port $FRONTEND_PORT
  rm -f "$LOG_DIR"/{backend,frontend}.pid
  echo "$(_ok) 已停"
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_logs() {
  local which="${1:-backend}"
  case "$which" in
    backend|b)  tail -f "$BACKEND_LOG" ;;
    frontend|f) tail -f "$FRONTEND_LOG" ;;
    *)          echo "usage: $0 logs [backend|frontend]"; exit 1 ;;
  esac
}

# ── main ───────────────────────────────────────────────────────────
case "${1:-status}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  logs)    cmd_logs "${2:-backend}" ;;
  *)       echo "usage: $0 {start|stop|restart|status|logs [backend|frontend]}"; exit 1 ;;
esac
