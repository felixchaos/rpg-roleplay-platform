# RPG Server — 生产部署指南

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

## PgBouncer 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `pool_mode` | transaction | 事务结束归还连接 |
| `max_client_conn` | 1000 | 客户端并发连接上限 |
| `default_pool_size` | 20 | 每 (user,db) server 连接数 |
| `min_pool_size` | 5 | 保活最小连接数 |
| `reserve_pool_size` | 5 | 突发高峰预留 |
| `query_wait_timeout` | 30 | 对齐 RPG_REQUEST_TIMEOUT_SECS |
