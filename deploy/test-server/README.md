# RPG Roleplay 测试服 — ECS06 部署 Runbook

> **仅限测试服（Docker on ECS06）。**
> 寻找裸机生产部署指引？见 [../bare-metal/README.md](../bare-metal/README.md)。

> 域名:`rpg-roleplay.stellatrix.icu`
> IP 白名单:`154.29.152.100`(三层防护)
> SSL:Cloudflare orange-cloud + CF Origin certificate(15 年)
> 主仓库:`/opt/rpg-roleplay/`

---

## §1. 三层 IP 白名单设计

| 层 | 位置 | 规则 | 失效后果 |
|---|---|---|---|
| L1 边缘 | **Cloudflare WAF Firewall Rule** | `http.host eq "rpg-roleplay.stellatrix.icu" and not ip.src eq 154.29.152.100` → block | 流量根本到不了源站 |
| L2 网络 | **ECS06 iptables/ufw** | 443/80 INPUT 只 ACCEPT CF IP 段 | 防"绕过 CF 直打源站 IP" |
| L3 应用 | **nginx `if ($remote_addr != "154.29.152.100") return 451`** | 兜底返 451 | 防 CF WAF 配错 |

L3 已在 `nginx-rpg-roleplay.conf` 配死;L1/L2 需要在 ECS06 + Cloudflare dashboard 配。

---

## §2. 前置准备(本地)

```bash
# 1. 准备 .env(填密码 + key)
cd deploy/test-server/
cp .env.example .env
$EDITOR .env
# 必填:
#   POSTGRES_PASSWORD=$(openssl rand -base64 32)
#   REDIS_PASSWORD=$(openssl rand -base64 32)
#   RPG_MASTER_KEY=$(openssl rand -hex 32)
#   RPG_SETUP_TOKEN=$(openssl rand -hex 32)
#   RESEND_API_KEY=re_xxx     # 复用 landing 配的
```

---

## §3. Cloudflare 配置(用户 dashboard 操作)

### 3.1 DNS 记录
- 类型:`A`
- 名称:`rpg-roleplay`
- 值:**ECS06 公网 IP**
- Proxy status:🟧 **Proxied**(orange-cloud)
- TTL:Auto

### 3.2 SSL/TLS 模式
- SSL/TLS → Overview → Encryption mode:**Full (strict)**

### 3.3 Origin Server 证书(15 年自签 CF 承认)
- SSL/TLS → Origin Server → Create Certificate
- Hostnames:`*.stellatrix.icu` + `stellatrix.icu`(通配复用)
- 有效期:15 年
- 下载得到:
  - `*.stellatrix.icu` certificate(public)
  - `*.stellatrix.icu` private key
- 命名保存到 ECS06:
  - `/opt/rpg-roleplay/certs/rpg-roleplay.cert.pem`
  - `/opt/rpg-roleplay/certs/rpg-roleplay.key.pem`
  - 权限:`chmod 644 cert.pem`, `chmod 600 key.pem`

### 3.4 WAF Firewall Rule(L1 IP 白名单)
- Security → WAF → Custom rules → Create rule
- Rule name:`rpg-roleplay-ip-allowlist`
- Expression:
  ```
  (http.host eq "rpg-roleplay.stellatrix.icu" and not ip.src eq 154.29.152.100)
  ```
- Action:`Block`
- Order:置顶

### 3.5 (可选)Always Use HTTPS
- SSL/TLS → Edge Certificates → Always Use HTTPS:**On**

---

## §4. ECS06 准备(SSH 上 ECS06)

```bash
# 系统更新 + 装 docker + 装 ufw
ssh ubuntu@ECS06_IP
sudo apt update && sudo apt install -y docker.io docker-compose-plugin ufw curl
sudo systemctl enable --now docker

# L2 iptables/ufw — 只让 CF IP 段进 443/80
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh                 # 保留 SSH(可改 limit 限速)

# Cloudflare IPv4 段(2024)
for ip in 173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 \
          141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 \
          197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 \
          104.24.0.0/14 172.64.0.0/13 131.0.72.0/22; do
  sudo ufw allow from $ip to any port 443 proto tcp
  sudo ufw allow from $ip to any port 80 proto tcp
done
sudo ufw enable
sudo ufw status numbered
```

⚠️ 验证 CF IP 段最新清单:`curl https://www.cloudflare.com/ips-v4`。

---

## §5. 部署源码 + 启动

```bash
# 1. clone 代码(切到目标分支 — 当前 audit 分支 frontend/list-detail-split-ui)
sudo mkdir -p /opt/rpg-roleplay
sudo chown ubuntu:ubuntu /opt/rpg-roleplay
cd /opt/rpg-roleplay
git clone <repo-url> .
git checkout frontend/list-detail-split-ui   # 或你想跑的版本

# 2. 放证书
sudo mkdir -p deploy/test-server/certs
# (从本地 scp 上传或直接粘贴)
sudo nano deploy/test-server/certs/rpg-roleplay.cert.pem
sudo nano deploy/test-server/certs/rpg-roleplay.key.pem
sudo chmod 644 deploy/test-server/certs/rpg-roleplay.cert.pem
sudo chmod 600 deploy/test-server/certs/rpg-roleplay.key.pem

# 3. 拷 .env(从本地用 scp/sftp 上传,确保权限)
# scp -i ~/.ssh/ecs06_id deploy/test-server/.env ubuntu@ECS06:/opt/rpg-roleplay/deploy/test-server/.env
chmod 600 deploy/test-server/.env

# 4. 构建 + 起
cd deploy/test-server/
docker compose up -d --build

# 5. 看启动日志
docker compose logs -f backend
# 等到看到 "uvicorn running on http://0.0.0.0:7860" 且 v37 migration 完成
```

---

## §6. 首次 admin 注册

```bash
# 6.1 在白名单 IP(154.29.152.100)上访问:
#   https://rpg-roleplay.stellatrix.icu/
#   → 应显示 Login.html(不是 451)
#
# 6.2 从 Login 页 → 注册:
#   - 填用户名 / 密码 / 邮箱 / 生日 / 勾选条款 + 年龄
#   - 同时在 form 里把 setup_token 字段填上(同 .env RPG_SETUP_TOKEN)
#   - 提交后会收 Resend 邮件验证码(从 noreply@stellatrix.icu 发)
#   - 验证码输入后第一个账号自动拿 admin
#
# 6.3 admin 注册完成后,**立刻**:
ssh ubuntu@ECS06
cd /opt/rpg-roleplay/deploy/test-server/
# 编辑 .env 把 RPG_SETUP_TOKEN= 改为空
nano .env
docker compose up -d --no-deps backend  # 只重启 backend 加载新 env
```

---

## §7. 健康检查 / 验证

```bash
# 在 ECS06 上:
docker compose ps                              # 6 容器 healthy
curl -k https://localhost/api/state            # 应返 JSON
curl https://localhost/api/state               # 不传 CF-Connecting-IP 应被 nginx 拦 451

# 在白名单 IP(154.29.152.100)主机上:
curl https://rpg-roleplay.stellatrix.icu/api/state   # 200
curl -v https://rpg-roleplay.stellatrix.icu/         # 看 HTTP 200 + index.html

# 在任意非白名单 IP:
curl https://rpg-roleplay.stellatrix.icu/            # CF WAF 直接 block(看不到)
```

---

## §8. 常用运维命令

```bash
cd /opt/rpg-roleplay/deploy/test-server/

# 看日志
docker compose logs -f backend
docker compose logs -f nginx
docker compose logs -f postgres

# 重启单个服务
docker compose restart backend
docker compose restart nginx

# 拉新代码 + 重建
cd /opt/rpg-roleplay/
git pull
cd deploy/test-server/
docker compose up -d --build backend cron

# 手动跑 cron(测试)
docker compose exec backend python -m rpg.scripts.run_cron all

# 进 backend shell
docker compose exec backend /bin/sh

# psql
docker compose exec postgres psql -U rpg -d rpg

# 备份 DB
docker compose exec -T postgres pg_dump -U rpg -Fc rpg > backup-$(date +%F).dump
```

---

## §9. 故障排查

| 症状 | 原因 | 解 |
|---|---|---|
| 浏览器超时 | CF DNS 还没 propagate(常见 5min)| `dig rpg-roleplay.stellatrix.icu` 确认 |
| 浏览器 451 | 不是从白名单 IP 来 | 用白名单 IP / 看 CF WAF 规则是否生效 |
| 浏览器 502 | backend 还没 ready | `docker compose logs -f backend` 看启动 |
| 注册收不到验证码 | RESEND_API_KEY 未配 或 DNS SPF/DKIM 未设 | 看 backend log;邮件落垃圾箱;Resend dashboard 看投递 |
| Splash 不弹 | `/api/me/splash/status` 未返;登录态问题 | F12 看 fetch 错误 |
| `npm install` 在 build 阶段失败 | 网络墙 | 配 npm 镜像或 docker buildx --build-arg http_proxy=... |

---

## §10. 上线前最终签字单(摘自 CODE_COMPLIANCE_CHECKLIST)

- [ ] 所有 P0 验收测试通过
- [ ] DMCA 代理在美国版权局目录可见(`dmca.copyright.gov/osp/`)
- [ ] 五运营邮箱投递:`legal@/privacy@/abuse@/security@/support@`
- [ ] CSAM SOP 端到端演练至少一次
- [ ] landing 政策文档"生效日期"从草案改为实际上线日期
- [ ] 备份 + 恢复演练
- [ ] 渗透测试(可后置)
- [ ] 主密钥离机备份(不同信任域)
