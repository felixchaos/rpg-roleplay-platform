# RPG Server — 生产部署指南

## 部署方式选择

| 方式 | 适用场景 | 文档 |
|------|---------|------|
| **裸机 / VPS** | 单机、中小流量、无 Docker | [deploy/bare-metal/README.md](./bare-metal/README.md) ← **推荐起点** |
| **Docker Compose** | 本地开发、测试服 | 本文档 §快速启动 + [test-server/README.md](./test-server/README.md) |
| **Kubernetes** | 多副本水平扩展、高可用 | 本文档 §Kubernetes 部署 |

> 新部署请先看 [bare-metal/README.md](./bare-metal/README.md)。它涵盖：Python/uvicorn 启动、PgBouncer 分离（migration 直连 5432，运行时走 6432）、systemd service、数据红线、升级流程。

---

## ⚠️ PgBouncer 与 Migration 注意事项

**migration 不能走 PgBouncer**。`platform_app.migrate` 内部使用 `pg_advisory_lock`，
而 PgBouncer transaction 模式不支持会话级特性（advisory lock / `LISTEN` / `SET`）。

- **跑 migration 时**：`DATABASE_URL` 必须指向直连 Postgres 5432
- **运行时 uvicorn**：`DATABASE_URL` 可走 PgBouncer 6432（事务级连接池）

若 Docker Compose 的 `DATABASE_URL` 指向 pgbouncer:6432，启动前请先用直连跑一次 `migrate full`，
或确保 `RPG_SKIP_AUTO_MIGRATE=1` 且 docker exec 进容器用直连 URL 跑 migrate。

> **pgvector**：`migrate full` 会在跑 versioned migrations *之前* 自动 `create extension if not exists vector`
> （`pgvector/pgvector:pg16` 镜像里 `rpg` 用户是该库 superuser，建扩展可成功），所以 docker 部署无需
> 手动建扩展。若用的是无建扩展权限的托管 Postgres，请先以管理员执行 `CREATE EXTENSION vector;` 再迁移，
> 否则向量列会被静默跳过、语义检索退化为关键词匹配。

---

## 架构概览

```
                    ┌─────────────────────────────────────┐
                    │           Kubernetes 集群             │
                    │                                     │
  Internet/LB ─────►  rpg-server x3~10 (Port 7860)       │
                    │    │  Axum + sqlx                   │
                    │    │  /livez /readyz /metrics        │
                    │    │                                 │
                    │    ▼                                 │
                    │  pgbouncer (Port 6432)               │
                    │    │  transaction 模式               │
                    │    │  max_client_conn=1000           │
                    │    │  default_pool_size=20           │
                    │    │                                 │
                    │    ├──────────────────────────────┐  │
                    │    ▼                              │  │
                    │  PostgreSQL 16 + pgvector         │  │
                    │    (max_connections ≤ 100)        │  │
                    │                                   │  │
                    │  Redis 7 ◄────────────────────────┘  │
                    │    (限流后端)                         │
                    └─────────────────────────────────────┘
```

## 目录结构

```
deploy/
├── Dockerfile                 # 多阶段构建(rust:1.83 → debian-slim)
├── docker-compose.yml         # 本地/测试环境一键启动
├── pgbouncer.ini              # pgbouncer 配置(transaction 模式)
├── userlist.txt               # pgbouncer 用户密码模板
├── README.md                  # 本文档
└── k8s/
    ├── configmap.yaml         # ConfigMap + Secret 模板
    ├── deployment.yaml        # rpg-server Deployment(3 副本)
    ├── service.yaml           # ClusterIP Services + Namespace
    ├── hpa.yaml               # HPA(3-10 副本,CPU+自定义指标)
    └── pgbouncer-deployment.yaml  # PgBouncer Deployment + ConfigMap
```

## 为何使用 PgBouncer

### 问题

sqlx 的连接池 (`max_connections`) 在每个进程内独立计数。k8s 水平扩展时:
- 3 副本 × pool_size=20 = **60 个 server 连接**
- 10 副本 × pool_size=20 = **200 个 server 连接**(已超 PG 默认限制)

PostgreSQL 每个连接消耗约 5-10MB 内存 + 一个进程,连接数过多导致:
- PG OOM / 性能崩溃
- 连接排队超时,用户看到错误

### PgBouncer 解法(transaction 模式)

```
10 副本 × sqlx pool_size=5 = 50 客户端连接
        ↓
   pgbouncer (max_client_conn=1000, pool_size=20)
        ↓
   PostgreSQL 实际 server 连接 ≤ 20
```

- **transaction 模式**:事务结束即归还 server 连接,适合短事务高并发
- **sqlx 兼容性**:sqlx acquire/release 完全匹配 transaction 边界
- **注意**:transaction 模式下不支持 `SET` / advisory locks / `LISTEN` 等会话级特性

## 快速启动(docker-compose)

### 前置条件

- Docker + Docker Compose v2
- 复制并填写 env 文件

```bash
cp .env.example .env
# 编辑 .env,填写 POSTGRES_PASSWORD / REDIS_PASSWORD / ANTHROPIC_API_KEY 等
```

### 启动

```bash
cd deploy/
# 首次启动(含镜像构建)
docker compose up --build -d

# 查看日志
docker compose logs -f backend

# 健康检查
curl http://localhost:7860/livez
curl http://localhost:7860/readyz

# 停止
docker compose down
```

## Kubernetes 部署

### 前置条件

- kubectl 已配置集群访问
- (可选) prometheus-adapter —— 用于 HPA 自定义指标

### 部署步骤

```bash
# 1. 创建 namespace
kubectl apply -f k8s/service.yaml  # 含 Namespace 定义

# 2. 创建 Secrets(替换为真实值)
kubectl create secret generic rpg-server-secrets \
  --from-literal=POSTGRES_PASSWORD=<your_pg_password> \
  --from-literal=REDIS_PASSWORD=<your_redis_password> \
  --from-literal=ANTHROPIC_API_KEY=<your_anthropic_key> \
  --from-literal=EMBED_API_KEY=<your_embed_key> \
  --from-literal=EMBED_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/ \
  --from-literal=RPG_CORS_ORIGINS=https://your-domain.com \
  -n rpg

# 3. 部署 ConfigMap
kubectl apply -f k8s/configmap.yaml

# 4. 部署 PgBouncer
kubectl apply -f k8s/pgbouncer-deployment.yaml

# 5. 部署 rpg-server
kubectl apply -f k8s/deployment.yaml

# 6. 部署 HPA
kubectl apply -f k8s/hpa.yaml

# 7. 验证
kubectl get pods -n rpg
kubectl get hpa -n rpg
```

### 构建镜像

```bash
# 从项目根目录构建
docker build -f deploy/Dockerfile -t rpg-server:latest .

# 推送到 registry
docker tag rpg-server:latest <your-registry>/rpg-server:v1.0.0
docker push <your-registry>/rpg-server:v1.0.0
```

## 环境变量清单

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `DATABASE_URL` | 是 | — | 指向 pgbouncer:6432(k8s 下) |
| `REDIS_URL` | 否 | — | redis://:password@host:6379 |
| `RPG_PORT` | 否 | 7860 | 监听端口 |
| `RPG_HOST` | 否 | 0.0.0.0 | 监听地址 |
| `RPG_CORS_ORIGINS` | 是(生产) | — | 允许的跨域来源 |
| `RPG_RATE_LIMIT_PER_MIN` | 否 | 100 | 每分钟限流阈值 |
| `RPG_REQUEST_TIMEOUT_SECS` | 否 | 30 | 请求超时(秒) |
| `RPG_BODY_LIMIT_BYTES` | 否 | 2097152 | 请求体大小限制(2MB) |
| `RPG_UPLOAD_BODY_LIMIT_BYTES` | 否 | 52428800 | 上传路由限制(50MB) |
| `RPG_COOKIE_SAMESITE` | 否 | lax | Cookie SameSite 策略 |
| `RPG_COOKIE_SECURE` | 否 | 1 | Cookie Secure 标志 |
| `RPG_SKIP_AUTO_MIGRATE` | 否 | 0 | 跳过自动迁移(设为 1) |
| `ANTHROPIC_API_KEY` | 是 | — | Claude API 密钥 |
| `EMBED_API_KEY` | 是 | — | Embedding API 密钥 |
| `EMBED_BASE_URL` | 是 | — | Embedding 服务地址 |
| `EMBED_MODEL` | 否 | text-embedding-004 | Embedding 模型名 |
| `RUST_LOG` | 否 | rpg_server=info | 日志级别 |

## 扩缩容说明

### HPA 触发条件

| 指标 | 扩容阈值 | 缩容阈值 |
|------|---------|---------|
| CPU 利用率 | > 70% | < 40% |
| 内存利用率 | > 80% | — |
| HTTP RPS(每 Pod) | > 500 | < 200 |

### 扩缩容策略

- **扩容**:触发后 60s 内稳定,每 30s 最多新增 2 个副本
- **缩容**:需持续 300s 低负载才触发,每 120s 最多移除 1 个副本(SSE 长连接友好)

### 优雅 Shutdown 流程

```
SIGTERM
  │
  ├─ Axum with_graceful_shutdown 停止接受新请求
  ├─ shutdown_token.cancel() 广播取消信号
  ├─ TaskTracker.wait() 等所有 spawned task 完成
  ├─ dirty game states flush 到 DB
  ├─ mcp_broker 停止
  └─ sqlx pool 关闭
     (terminationGracePeriodSeconds=60,覆盖全流程)
```

## 前端生产部署 Web 性能配置

本节记录推荐的网络层优化手段，供 nginx/CDN 配置参考。**以下均为文档化最佳实践，不强制要求逐条实施。**

### HTTP/3 (QUIC)

```nginx
server {
    listen 443 quic reuseport;
    listen 443 ssl;
    http2 on;
    http3 on;
    quic_retry on;

    ssl_certificate     /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;

    # 告知客户端支持 h3，缓存 1 天
    add_header Alt-Svc 'h3=":443"; ma=86400' always;
}
```

需要 nginx >= 1.25.0（主线版）且编译时带 `--with-http_v3_module`。

### Brotli / Gzip 压缩

后端（rpg-server）已内置 Axum + tower-http `CompressionLayer`，支持 Brotli 与 Gzip，
自动根据客户端 `Accept-Encoding` 选择算法，SSE 流已通过 predicate 排除压缩以防缓冲。

若前端静态文件由 nginx 直接 serve（非后端代理），可额外开启 nginx Brotli：

```nginx
# 需 ngx_brotli 模块
brotli on;
brotli_comp_level 6;
brotli_types text/html text/css application/javascript application/json image/svg+xml;

# Gzip 兜底
gzip on;
gzip_comp_level 6;
gzip_types text/html text/css application/javascript application/json;
```

### Early Hints (103)

在 nginx 发送完整响应前提前推送关键资源链接，减少渲染阻塞：

```nginx
location / {
    # 提前告知浏览器预加载核心 CSS / JS
    add_header Link "</assets/react-vendor-[hash].js>; rel=preload; as=script" always;
    add_header Link "</assets/index-[hash].css>; rel=preload; as=style" always;
    # 103 Early Hints 由 nginx >= 1.27.x 或 Caddy 原生支持
}
```

### CDN 边缘缓存策略

Vite build 已启用 asset hash 文件名（`[name]-[hash][extname]`），内容变更时 hash 自动更换，
可安全配置超长缓存：

```nginx
# hash 文件名的静态资源：永不过期
location /assets/ {
    add_header Cache-Control "public, max-age=31536000, immutable";
    expires 1y;
}

# HTML 入口：禁止缓存，确保部署后立即生效
location ~* \.(html)$ {
    add_header Cache-Control "no-cache, no-store, must-revalidate";
    expires 0;
}
```

CDN 厂商（CloudFront、Cloudflare、阿里云 CDN）配置相同的 `Cache-Control` 即可在边缘节点缓存。

### 字体子集化

减少中文字体包体积，仅保留实际用到的字形（中英文 + 拉丁扩展）：

```bash
# 安装工具
pip install fonttools brotli

# 使用 Unicode 范围子集化（CJK 统一汉字基本区 + ASCII + 拉丁扩展）
pyftsubset NotoSansSC.ttf \
  --unicodes="U+0020-007E,U+00A0-00FF,U+4E00-9FFF,U+3000-303F,U+FF00-FFEF" \
  --flavor=woff2 \
  --output-file=NotoSansSC-subset.woff2

# 验证体积压缩率（通常可从 10MB+ 降至 200KB 以内）
ls -lh NotoSansSC-subset.woff2
```

### 图片现代格式（AVIF / WebP）

```bash
# AVIF 编码（需 libavif）
avifenc --min 20 --max 40 input.jpg output.avif

# WebP 编码（需 libwebp）
cwebp -q 80 input.jpg -o output.webp
```

HTML 中使用 `<picture>` 渐进增强，浏览器按支持情况自动选择：

```html
<picture>
  <source srcset="image.avif" type="image/avif">
  <source srcset="image.webp" type="image/webp">
  <img src="image.jpg" alt="描述" loading="lazy" decoding="async">
</picture>
```

---

## PgBouncer 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `pool_mode` | transaction | 事务结束归还连接 |
| `max_client_conn` | 1000 | 客户端并发连接上限 |
| `default_pool_size` | 20 | 每 (user,db) server 连接数 |
| `min_pool_size` | 5 | 保活最小连接数 |
| `reserve_pool_size` | 5 | 突发高峰预留 |
| `query_wait_timeout` | 30 | 对齐 RPG_REQUEST_TIMEOUT_SECS |

---

## e2e 集成测试

Wave 9-B 引入 `rpg-server/tests/e2e.rs`,真打 Postgres + 走 axum Router 验关键 API 路径。
默认 **不在 CI 跑**(无 docker 时会炸),通过 `e2e` cargo feature + `#[ignore]` 双保险。

### 依赖

- Docker / docker compose
- 端口 55432(避开本地 5432 + deploy 这套)

### 跑法

```bash
# 1) 起独立 Postgres 容器(pgvector/pg16,无 pgbouncer 无 redis,最小 stack)
cd rust && docker compose -f docker-compose.e2e.yml up -d

# 2) 等 healthy(~3s)
docker compose -f docker-compose.e2e.yml ps

# 3) 设 DB url + 跑 e2e
export RPG_TEST_DB_URL=postgres://rpg:changeme@localhost:55432/rpg_e2e
cargo test -p rpg-server --features e2e -- --ignored

# 4) 收
cd rust && docker compose -f docker-compose.e2e.yml down -v
```

### 设计要点

- **schema 隔离**:每个测试在独立 schema(`e2e_<8hex>`)跑 migrations,DROP CASCADE 收尾;
  并行跑互不污染,失败留 schema 便于事后查。
- **HTTP 走 oneshot**:`tower::ServiceExt::oneshot` 打 `build_regular_routes()` 返回的
  Router,不起真实端口 → 无端口冲突 / 无 governor 速率限制干扰。
- **LLM 不打真 API**:`/api/chat` 只验匿名 401;真测 SSE chunk 需 wiremock 起 mock
  Anthropic endpoint(留 Wave 9-C)。
- **RPG_TEST_DB_URL 未设 → 测试早返回不 fail**:本地手贱 `cargo test --features e2e`
  也不会红。

### CI 集成建议

GitHub Actions 上跑可:

```yaml
- name: 起 Postgres + e2e
  run: |
    cd rust && docker compose -f docker-compose.e2e.yml up -d
    for i in 1 2 3 4 5 6 7 8 9 10; do
      docker exec rpg-postgres-e2e pg_isready -U rpg -d rpg_e2e && break
      sleep 1
    done
    export RPG_TEST_DB_URL=postgres://rpg:changeme@localhost:55432/rpg_e2e
    cargo test -p rpg-server --features e2e -- --ignored --nocapture
```

