# ============================================================
# RPG Roleplay — Python (FastAPI/uvicorn) 生产镜像
# Stage 1 (frontend): node:20 — vite build → frontend/dist
# Stage 2 (runtime):  python:3.14-slim — pip install + uvicorn
#
# 注意:main 分支已切回 Python 产品线(rust/ 归档在 archive/rust-migration)。
# 旧的 Rust 版 Dockerfile 保留在该归档分支。
# ============================================================

# ─── Stage 1: 构建前端 dist ──────────────────────────────────────────────────
# 前端是 Vite ESM,必须先 build 产出 frontend/dist(打包后的 /assets/*.js);
# 后端 app.py 同源挂载 frontend/dist。
FROM node:20-slim AS frontend

WORKDIR /build/frontend
# 先复制 lockfile 利用 layer cache
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# ─── Stage 2: Python 运行时 ──────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# 运行时库 + 编译依赖兜底(Python 3.14 较新,个别包可能无预编译 wheel)。
# libpq-dev/gcc 用于 pip 编译;运行时 psycopg-binary 自带 libpq。
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gcc libpq-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 非 root 运行
RUN groupadd -r rpg && useradd -r -g rpg -s /bin/false rpg

WORKDIR /app

# 先装依赖(利用 layer cache:requirements 不变则跳过)
COPY rpg/requirements.txt /app/rpg/requirements.txt
RUN pip install --no-cache-dir -r /app/rpg/requirements.txt

# 后端代码
COPY rpg/ /app/rpg/
# 前端源 + 从 stage 1 拷入构建产物 dist
COPY frontend/ /app/frontend/
COPY --from=frontend /build/frontend/dist /app/frontend/dist

RUN chown -R rpg:rpg /app
USER rpg

# app.py 的 import 用裸模块名(from app / from core.config),工作目录必须在 rpg/
WORKDIR /app/rpg

EXPOSE 7860

# 健康检查:/api/state 始终 200(匿名也返回基础状态)
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -sf http://localhost:7860/api/state || exit 1

# Docker stop → SIGTERM → uvicorn 优雅 drain
STOPSIGNAL SIGTERM

# 多 worker(吃满多核;6核默认 4 worker)。SSE 停止跨 worker 安全:platform_app.cluster
# 用 DB stop_signals 表做共享停止信号(in-process Event 是快路径)。
# 池预算:生产 DATABASE_URL 走 pgbouncer(事务池),RPG_DB_POOL_MAX × RPG_WORKERS 只是
#   到 pgbouncer 的廉价客户端连接(pgbouncer 复用到少量 postgres 连接);25×4 经 pgbouncer 无压。
# exec 让 uvicorn 直接收 SIGTERM(优雅 drain),env 可调 worker 数。
CMD ["/bin/sh", "-c", "exec python -m uvicorn app:app --host 0.0.0.0 --port 7860 --no-access-log --workers ${RPG_WORKERS:-4}"]
