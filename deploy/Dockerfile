# ============================================================
# 多阶段构建: rpg-server
# Stage 1 (builder): rust:1.83 — cargo build --release
# Stage 2 (runtime): debian-slim + ca-certs
# ============================================================

# ─── Stage 1: Builder ────────────────────────────────────────────────────────
FROM rust:1.83-slim-bookworm AS builder

# 安装编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 先复制 Cargo 清单,利用 layer cache 缓存依赖编译
# 只要 Cargo.toml/Cargo.lock 不变,依赖层不会重建
COPY rust/Cargo.toml rust/Cargo.lock ./
COPY rust/crates ./crates/

# 构建所有依赖(产生缓存层)——先创建空 main 占位,再完整构建
# 注: sqlx 需要 DATABASE_URL 或 offline 模式
ENV SQLX_OFFLINE=true

# 完整构建 release 二进制
RUN cargo build --release --bin rpg-server

# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

# 安装运行时必要库
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 运行用户
RUN groupadd -r rpg && useradd -r -g rpg -s /bin/false rpg

WORKDIR /app

# 从 builder 拷贝二进制
COPY --from=builder /build/target/release/rpg-server /app/rpg-server

# 拷贝 sqlx migrations(如果有)
COPY --from=builder /build/crates/rpg-db/migrations /app/migrations 2>/dev/null || true

RUN chmod +x /app/rpg-server && chown rpg:rpg /app/rpg-server

USER rpg

# 服务端口
EXPOSE 7860

# 健康检查(使用 /readyz)
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:7860/readyz || exit 1

# 优雅 shutdown: Docker stop 发送 SIGTERM,Axum 的 with_graceful_shutdown 监听并 drain
STOPSIGNAL SIGTERM

ENTRYPOINT ["/app/rpg-server"]
