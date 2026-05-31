# RPG 后端 — 裸机生产部署 Runbook

## 适用场景

- 单机 / VPS / 裸金属（无 Docker、无 Kubernetes）
- Ubuntu 22.04+ / Debian 12+
- 中小流量（< 200 并发用户）

## 架构概览

```
浏览器
  │ HTTPS 443
  ▼
nginx (反代 + SSL + 静态文件)
  │ http 127.0.0.1:7860
  ▼
uvicorn + FastAPI (rpg/ 下的 app.py)
  │ psycopg (运行时)          │ psycopg (migration 专用)
  ▼                           ▼
PgBouncer :6432            Postgres :5432 (直连)
(transaction 池)             ↑ migration 必须直连！
  │
  ▼
Postgres 16 + pgvector + pg_trgm
  +
Redis 7 (限流 / 缓存)
```

> **关键约束**：`pg_advisory_lock` 不能过 PgBouncer transaction 池。
> migration 时 DATABASE_URL 指向 5432，运行时才走 6432。详见 §3 和 §5。

---

## §1. 系统准备

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    python3.12 python3.12-venv python3.12-dev \
    postgresql-16 postgresql-16-pgvector postgresql-contrib \
    pgbouncer redis-server nginx ufw certbot python3-certbot-nginx \
    git curl build-essential
```

> Ubuntu 22.04 默认 Python 3.10，默认 apt 源也未必有 PostgreSQL 16 / pgvector。
> 如需 3.12 先加 deadsnakes PPA；如 apt 找不到 `postgresql-16*`，先接入 PostgreSQL PGDG apt 源。

---

## §2. Postgres 安装与配置

### 2.1 启用扩展

```bash
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

### 2.2 创建 rpg 用户 + 库

```bash
# 交互式设密码（记住，后面 .env 要用）
sudo -u postgres createuser --pwprompt rpg
sudo -u postgres createdb -O rpg rpg

# 在 rpg 库里启用扩展
sudo -u postgres psql -d rpg -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d rpg -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -d rpg -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
```

### 2.3 pg_hba.conf — 允许本地密码登录

```bash
# 找到 pg_hba.conf（通常 /etc/postgresql/16/main/pg_hba.conf）
sudo nano /etc/postgresql/16/main/pg_hba.conf

# 确保有如下行（host 连接用 scram-sha-256）：
# host    rpg             rpg             127.0.0.1/32            scram-sha-256
# local   all             postgres                                peer
```

```bash
sudo systemctl restart postgresql
```

### 2.4 内存调优建议（/etc/postgresql/16/main/postgresql.conf）

```ini
max_connections = 100            # PgBouncer 在前面，PG 不需要太多
shared_buffers = 512MB           # 约 25% RAM（2GB RAM 机器）
work_mem = 32MB
maintenance_work_mem = 128MB
effective_cache_size = 1536MB    # 约 75% RAM
```

```bash
sudo systemctl restart postgresql
```

### 2.5 定时备份（cron）

```bash
sudo mkdir -p /var/backups/rpg
sudo chown postgres:postgres /var/backups/rpg
sudo tee /etc/cron.d/rpg-backup <<'EOF'
# 每天凌晨 02:00 备份
0 2 * * * postgres pg_dump -Fc rpg > /var/backups/rpg/rpg-$(date +\%F).dump && \
    find /var/backups/rpg/ -name "*.dump" -mtime +14 -delete
EOF
```

---

## §3. PgBouncer 配置（运行时用 — migration 不经 PgBouncer）

### ⚠️ 重要：migration 绝对不走 PgBouncer

`platform_app.migrate` 内部使用 `pg_advisory_lock`。PgBouncer transaction 模式下，
advisory lock 在事务结束即释放连接时会丢失，导致并发迁移无法串行化，可能损坏 schema。

- **跑 migration**：DATABASE_URL = `postgresql://rpg:PASS@127.0.0.1:5432/rpg` （直连 PG）
- **运行时 uvicorn**：DATABASE_URL = `postgresql://rpg:PASS@127.0.0.1:6432/rpg` （PgBouncer）

### 3.1 /etc/pgbouncer/pgbouncer.ini

```ini
[databases]
rpg = host=127.0.0.1 port=5432 dbname=rpg

[pgbouncer]
listen_addr = 127.0.0.1
listen_port = 6432
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 20
min_pool_size = 5
reserve_pool_size = 5
reserve_pool_timeout = 3
query_wait_timeout = 30
server_idle_timeout = 600
ignore_startup_parameters = extra_float_digits
log_connections = 0
log_disconnections = 0
```

### 3.2 /etc/pgbouncer/userlist.txt

```bash
# Postgres 16 默认用 SCRAM。直接复用 pg_authid 中的 SCRAM secret。
sudo -u postgres psql -Atc \
  "select '\"' || rolname || '\" \"' || rolpassword || '\"' from pg_authid where rolname = 'rpg'" \
  | sudo tee /etc/pgbouncer/userlist.txt >/dev/null

sudo chown postgres:postgres /etc/pgbouncer/userlist.txt
sudo chmod 600 /etc/pgbouncer/userlist.txt
```

### 3.3 启动 PgBouncer

```bash
sudo systemctl enable --now pgbouncer
sudo systemctl status pgbouncer

# 验证：应能连上 6432
psql -U rpg -h 127.0.0.1 -p 6432 -d rpg -c "SELECT 1;"
```

---

## §4. 部署代码

### 4.1 克隆仓库

```bash
sudo mkdir -p /opt/rpg-roleplay
sudo chown $USER:$USER /opt/rpg-roleplay
cd /opt/rpg-roleplay
git clone <repo-url> .
git checkout main    # 或目标分支 / tag
```

### 4.2 Python 依赖（在 rpg/ 子目录）

```bash
# !! 必须进入 rpg/ 子目录 — 所有 python -m platform_app.xxx 依赖此 cwd !!
cd /opt/rpg-roleplay/rpg
python3.12 -m venv .venv
.venv/bin/pip install -U pip setuptools wheel
.venv/bin/pip install -r requirements.txt
```

### 4.3 前端 build

```bash
cd /opt/rpg-roleplay/frontend
npm install
npm run build    # 产物在 frontend/dist/
```

### 4.4 配置 .env

```bash
cd /opt/rpg-roleplay/rpg
# 若无 rpg/.env.example，从测试服模板复制
cp .env.example .env 2>/dev/null || cp ../deploy/test-server/.env.example .env
$EDITOR .env
```

**必填项**：

| 变量 | 说明 | 生成方式 |
|------|------|---------|
| `DATABASE_URL` | **首次配成直连 5432**（migration 用）；migration 完成后改 6432 见 §6 | `postgresql://rpg:PASS@127.0.0.1:5432/rpg` |
| `POSTGRES_PASSWORD` | 与 createuser 一致 | `openssl rand -base64 32` |
| `REDIS_PASSWORD` | — | `openssl rand -base64 32` |
| `RPG_MASTER_KEY` | 泄露则所有用户 API key 可解 | `openssl rand -hex 32` |
| `RPG_SETUP_TOKEN` | 首位 admin 注册令牌，用完即删 | `openssl rand -hex 32` |
| `RESEND_API_KEY` | 注册验证邮件 | Resend dashboard |
| `ANTHROPIC_API_KEY` | admin 兜底 LLM（普通用户 BYOK）| Anthropic console |
| `EMBED_BASE_URL` / `EMBED_MODEL` / `EMBED_API_KEY` | 向量 embedding 服务 | 取决于厂商 |
| `RPG_CORS_ORIGINS` | 生产域名，逗号分隔 | `https://your-domain.com` |

```bash
chmod 600 /opt/rpg-roleplay/rpg/.env
```

---

## §5. 首次 migration（fresh DB）

```bash
cd /opt/rpg-roleplay/rpg

# fresh DB 必须用 full，不能用 up
# full = baseline（CREATE TABLE）+ up（增量 migration）+ pgvector
DATABASE_URL=postgresql://rpg:CHANGE_ME@127.0.0.1:5432/rpg \
  .venv/bin/python -m platform_app.migrate full

# 验证：所有 v1..v38 migration 已应用
DATABASE_URL=postgresql://rpg:CHANGE_ME@127.0.0.1:5432/rpg \
  .venv/bin/python -m platform_app.migrate status
```

> **为什么不能用 `up` 做首次部署？**
> `up` 假设基线表已经存在。fresh DB 上直接跑 `up`
> 会因为 ALTER TABLE 目标表不存在而失败。`full` 先建基线表再跑增量。
>
> `platform_app.migrate` 不自动读取 `rpg/.env`。这里显式把 `DATABASE_URL` 写在命令前，
> 是为了确保 migration 走直连 5432，而不是运行时 PgBouncer 6432。

---

## §6. Systemd Service — uvicorn 后端

### 6.1 创建专用 system user

```bash
sudo useradd --system --no-create-home --shell /sbin/nologin rpg
sudo chown -R rpg:rpg /opt/rpg-roleplay
sudo chmod 750 /opt/rpg-roleplay
```

### 6.2 /etc/systemd/system/rpg-backend.service

```bash
sudo tee /etc/systemd/system/rpg-backend.service <<'EOF'
[Unit]
Description=RPG Roleplay Backend (uvicorn/FastAPI)
After=network.target postgresql.service redis-server.service pgbouncer.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=rpg
Group=rpg
WorkingDirectory=/opt/rpg-roleplay/rpg

EnvironmentFile=/opt/rpg-roleplay/rpg/.env

ExecStart=/opt/rpg-roleplay/rpg/.venv/bin/uvicorn app:app \
    --host 127.0.0.1 \
    --port 7860 \
    --workers 4 \
    --no-access-log \
    --timeout-keep-alive 75

Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=rpg-backend

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/rpg-roleplay

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rpg-backend
sudo systemctl status rpg-backend
```

> `.env` 中的运行时 `DATABASE_URL` 应指向 PgBouncer 6432，并设置
> `RPG_SKIP_AUTO_MIGRATE=1`。首次 migration 只在 §5 命令前临时 inline 直连 5432，
> 不要把 5432 写进长期运行的 service 环境。

---

## §7. Systemd Timer — cron 任务

```bash
sudo tee /etc/systemd/system/rpg-cron.service <<'EOF'
[Unit]
Description=RPG Cron (hard_delete / prune_audit / policy / feedback cleanup)

[Service]
Type=oneshot
User=rpg
Group=rpg
WorkingDirectory=/opt/rpg-roleplay
EnvironmentFile=/opt/rpg-roleplay/rpg/.env
Environment="PYTHONPATH=/opt/rpg-roleplay:/opt/rpg-roleplay/rpg"
ExecStart=/opt/rpg-roleplay/rpg/.venv/bin/python -m rpg.scripts.run_cron all
StandardOutput=journal
StandardError=journal
SyslogIdentifier=rpg-cron
EOF

sudo tee /etc/systemd/system/rpg-cron.timer <<'EOF'
[Unit]
Description=Run rpg cron jobs daily at 03:00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rpg-cron.timer
sudo systemctl list-timers rpg-cron.timer
```

---

## §8. nginx 反代 + SSL

### 8.1 Let's Encrypt（公网域名）

```bash
sudo certbot --nginx -d your-domain.com
```

### 8.2 Cloudflare Origin Server（CF 代理）

把证书上传到服务器，然后在 nginx 引用（见 §8.3）。

### 8.3 /etc/nginx/sites-available/rpg

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name your-domain.com;

    ssl_certificate     /etc/ssl/certs/rpg.cert.pem;
    ssl_certificate_key /etc/ssl/private/rpg.key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 静态前端
    root /opt/rpg-roleplay/frontend/dist;
    index Login.html;

    location / {
        try_files $uri $uri/ /Login.html;
    }

    # 后端 API（含 SSE — 关闭 buffering）
    location /api/ {
        proxy_pass         http://127.0.0.1:7860;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";

        # SSE 必须关闭 buffering，否则流式响应会卡死
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/rpg /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## §9. ufw 防火墙

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 443/tcp
sudo ufw allow 80/tcp
# PgBouncer / Postgres / Redis 不对外暴露（仅 127.0.0.1）
sudo ufw enable
sudo ufw status verbose
```

---

## §10. 健康验证

```bash
# 服务状态
sudo systemctl status rpg-backend pgbouncer postgresql redis-server nginx

# API 健康
curl https://your-domain.com/api/state

# 本机直连后端（跳过 nginx）
curl http://127.0.0.1:7860/api/state

# 查看实时日志
sudo journalctl -u rpg-backend -f
sudo journalctl -u rpg-cron -n 50
```

---

## §11. 后续升级（有新 commit）

```bash
cd /opt/rpg-roleplay

# !! 升级前先备份 DB !!
pg_dump -Fc rpg > /var/backups/rpg/rpg-pre-migrate-$(date +%F-%H%M).dump

git pull
cd rpg
.venv/bin/pip install -r requirements.txt    # 若有新依赖

cd ../frontend
npm install
npm run build

cd ../rpg

# migration 用直连 5432，不走 PgBouncer！
DATABASE_URL=postgresql://rpg:CHANGE_ME@127.0.0.1:5432/rpg \
    .venv/bin/python -m platform_app.migrate up

# 验证 migration 完成
.venv/bin/python -m platform_app.migrate status

sudo systemctl restart rpg-backend
sudo systemctl status rpg-backend
```

---

## §12. 🔴 红线 — 本地数据绝不传入生产

**以下路径绝不 rsync / scp 到生产服务器**（也不推送到 git）：

| 路径 | 原因 |
|------|------|
| `rpg/platform_data/` | 本地玩家数据，内含个人信息 |
| `rpg/saves/` | 本地存档（legacy） |
| `rpg/uploads/` | 本地上传文件 |
| `rpg/.venv/` | 本地虚拟环境，路径硬编码 |
| `frontend/dist/` | 本地 build 产物，重 build 即可 |
| `frontend/uploads/` | 本地上传文件 |
| `frontend/node_modules/` | 本地依赖，重 npm install 即可 |
| `.webnovel/` | 作者文学 IP，严禁外传 |
| `*.db` / `*.dump` / `*.sqlite` | 本地数据库文件 |
| `.env` / `vertex_sa.json` / `master.key` | 密钥 — 绝对不传！ |

**正确姿势**：只传 git tracked 文件。用 `git pull` 更新代码，`pip install` / `npm run build` 重建依赖。

---

## §13. 故障排查

| 症状 | 最可能原因 | 解法 |
|------|-----------|------|
| `platform_app.migrate` → `ModuleNotFoundError` | 在仓库根而非 `rpg/` 目录运行 | `cd rpg/ && .venv/bin/python -m platform_app.migrate ...` |
| migration 卡或报 `pg_advisory_lock` 错 | DATABASE_URL 指向 PgBouncer 6432 | 改成直连 5432 再跑 migrate |
| `up` 报目标表不存在 / DDL 失败 | fresh DB 上用了 `up` | 改用 `full`（首次部署） |
| uvicorn 启动报 `psycopg` 找不到 | 从错误 cwd 启动，venv 路径不对 | 确认 WorkingDirectory=/opt/rpg-roleplay/rpg |
| 5xx 大面积错误 | backend 未起 / DB 连不上 | `journalctl -u rpg-backend -f` 看栈 |
| SSE 流式响应卡死（长请求无返回） | nginx proxy_buffering 未关 | nginx location /api/ 加 `proxy_buffering off;` |
| 注册收不到验证码 | RESEND_API_KEY 未配 或 SPF/DKIM 未设 | Resend dashboard 看投递状态 |
| PgBouncer 报"unsupported startup parameter" | 客户端发了不兼容的连接参数 | pgbouncer.ini 加 `ignore_startup_parameters = extra_float_digits` |

---

## §14. 上线签字单

在正式对外开放前，逐项确认：

- [ ] `migrate status` 显示所有 migration 已应用（无待应用项）
- [ ] `systemctl status rpg-backend` 为 active (running)
- [ ] `curl https://your-domain.com/api/state` 返回 200 JSON
- [ ] nginx SSL 证书有效，无浏览器警告
- [ ] ufw 启用，5432 / 6432 / 6379 仅本地可达
- [ ] `.env` 权限 600，不在 git tracked 文件中
- [ ] `RPG_SETUP_TOKEN` 用完后已从 .env 清除，服务已重启
- [ ] 备份 cron 已启用并验证过一次手动 pg_dump 可恢复
- [ ] 主密钥（RPG_MASTER_KEY）已离机备份到不同信任域
- [ ] RESEND_API_KEY 已配，测试注册邮件可收到
- [ ] `RPG_CORS_ORIGINS` 配为生产域名（非 `*`）
- [ ] 本地数据路径（platform_data / saves / .webnovel 等）未混入服务器
