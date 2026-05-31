#!/usr/bin/env bash
# dev.sh — Python 版一键开发启动 (postgres / backend / frontend static server / preview)
# 注意:此脚本启动的是 Python 后端 (rpg/app.py)。
# Rust 后端请使用: cargo run -p rpg-server
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
  echo "  · graceful stop :$1 (pid=$pid)"
  # SIGTERM 让进程 (uvicorn / vite) 关 SSE / db connection / file watchers
  kill -15 "$pid" 2>/dev/null
  local i; for i in 1 2 3 4 5; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  echo "  · force kill :$1 (pid=$pid) — graceful 5s 超时"
  kill -9 "$pid" 2>/dev/null
  sleep 0.5
  # 子进程 (uvicorn reloader 的 worker / vite 的 esbuild) 也要清
  pkill -9 -P "$pid" 2>/dev/null
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
# 后端: uvicorn --reload 监听 .py 改动自动重启 worker,无需手动 restart
# 前端: npm run dev (vite) 内置 HMR,改 jsx 自动热更新页面不刷新
start_backend() {
  if [ -n "$(_pid_on_port $BACKEND_PORT)" ]; then
    echo "  $(_warn) backend :$BACKEND_PORT 已运行 — 跳过 (用 restart 强重启)"
    return 0
  fi
  if [ ! -x "$RPG_DIR/.venv/bin/uvicorn" ]; then
    echo "  $(_bad) .venv/bin/uvicorn 不存在 — 先 cd rpg && python -m venv .venv && .venv/bin/pip install -r requirements.txt"
    return 1
  fi
  echo "  · 启动 backend (uvicorn --reload,改 .py 自动重启) → $BACKEND_LOG"
  (
    cd "$RPG_DIR"
    # --reload 监控 . (rpg/) 下所有 .py 改动
    # --reload-exclude 跳过测试 / 节点产物,避免误触发(尤其 .venv 大量 .py)
    # 注意: uvicorn --reload 不支持 workers > 1,DEV 单 worker 是正确配置
    nohup .venv/bin/uvicorn app:app \
      --host 127.0.0.1 \
      --port "$BACKEND_PORT" \
      --reload \
      --reload-dir . \
      --reload-exclude '.venv/*' \
      --reload-exclude 'tests/*' \
      --reload-exclude '__pycache__/*' \
      --reload-exclude '*.pyc' \
      --reload-exclude 'platform_data/*' \
      --log-level info \
      > "$BACKEND_LOG" 2>&1 &
    echo "$!" > "$LOG_DIR/backend.pid"
  )
  # 等到 200 或 12s timeout (reload 模式启动比直跑 python app.py 慢一点)
  local i; for i in $(seq 1 30); do
    sleep 0.5
    local code; code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$BACKEND_PORT/" 2>/dev/null)
    [ "$code" = "200" ] && { echo "  $(_ok) backend ready in ~${i}*0.5s (改 rpg/*.py 自动重启)"; return 0; }
  done
  echo "  $(_bad) backend 15s 内没起来,看 $BACKEND_LOG"
  tail -10 "$BACKEND_LOG" | sed 's/^/    /'
  return 1
}

# vite dev server: 真正 HMR,改 jsx/css 不刷新页面就热替换
# 与原 python -m http.server 的区别:
#   - 原:静态文件服务器,改文件后必须手动 F5
#   - 现:vite,改文件自动 HMR (不刷新);改 vite.config 才需要 dev 重启
start_frontend() {
  if [ -n "$(_pid_on_port $FRONTEND_PORT)" ]; then
    echo "  $(_warn) frontend :$FRONTEND_PORT 已运行 — 跳过 (用 restart 强重启)"
    return 0
  fi
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "  $(_warn) frontend/node_modules 缺失 — 自动 npm install (首次约 30-60s)"
    ( cd "$FRONTEND_DIR" && npm install ) || {
      echo "  $(_bad) npm install 失败"; return 1
    }
  fi
  echo "  · 启动 frontend (vite,HMR 自动热更新) → $FRONTEND_LOG"
  (
    cd "$FRONTEND_DIR"
    # --host 127.0.0.1 限本地;--port 跟旧约定 5173;--strictPort 端口被占报错而不是自动找空闲
    nohup npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort > "$FRONTEND_LOG" 2>&1 &
    echo "$!" > "$LOG_DIR/frontend.pid"
  )
  # vite 冷启动比 python http.server 慢 (要做 deps 预编译),给 20s
  local i; for i in $(seq 1 40); do
    sleep 0.5
    local code; code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$FRONTEND_PORT/Platform.html" 2>/dev/null)
    [ "$code" = "200" ] && { echo "  $(_ok) frontend ready in ~${i}*0.5s (改 jsx/css 自动 HMR)"; return 0; }
  done
  echo "  $(_bad) frontend 20s 内没起来,看 $FRONTEND_LOG"
  tail -15 "$FRONTEND_LOG" | sed 's/^/    /'
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
  echo "─── 启动 dev 环境 (热重载已开启) ───"
  check_postgres || { echo "$(_bad) Postgres 没起,先解决。"; exit 1; }
  start_backend  || exit 1
  start_frontend || exit 1
  echo ""
  echo "$(_ok) 全部就绪 →  http://127.0.0.1:$FRONTEND_PORT/Platform.html"
  echo ""
  echo "  $(_color 36 "·") 改 rpg/*.py    → uvicorn 自动重启 (1-3s)"
  echo "  $(_color 36 "·") 改 frontend/   → vite HMR 自动热更新 (无需刷新)"
  echo "  $(_color 36 "·") 看实时日志    → $0 logs [backend|frontend]"
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
