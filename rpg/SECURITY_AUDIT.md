# RPG 平台安全审计

审计时间：2026-05-24
审计范围：`/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/rpg/`（不含正文小说）
审计人员：Claude Code (Opus)

---

## 1. 部署场景与威胁模型

### 1.1 三个部署场景

| 场景 | 用户数 | 鉴权 | 主要威胁 |
|------|--------|------|----------|
| **本地单机** | 1（开发者本人） | 不强制 | 主要是文件误删 / 误存 / API key 泄露 |
| **自托管多用户** | 5-50（家人/小团队） | 强制登录 | 横向越权、SSO 滥用、共享资源越界 |
| **公网部署** | 不可知 | 强制 + WAF/RL | 注入、暴力破解、信息窃取、资源滥用、模型滥用 |

### 1.2 资产与攻击者画像

| 资产 | 价值 | 攻击场景 |
|------|------|----------|
| Anthropic / Vertex API Key | 高（计费） | 越权调用模型、刷 token |
| 玩家存档 / 剧本内容 | 中（创作 IP） | 越权读取他人剧情 |
| 上传的剧本文件 | 中 | zip-slip 写入主项目目录 |
| PostgreSQL | 高 | 越权读写、删表 |
| Skill 上传通道 | 高（代码执行） | RCE 通过恶意 SKILL |
| MCP server 配置 | 高（代码执行） | 配置恶意 command 实现 RCE |

攻击者画像：
- A1：未授权远程攻击者（公网部署相关）
- A2：低权限用户（多用户部署，普通用户尝试越权）
- A3：好奇的本地用户（误操作）
- A4：恶意上传（Skill zip / 剧本文件）

---

## 2. 已实施的缓解措施

### 2.1 鉴权与授权（A1, A2）

**实现位置**：`platform_app/auth.py` + `ui.py:_require_api_user`

| 控制项 | 实现 |
|--------|------|
| 密码哈希 | bcrypt via `bcrypt.hashpw` |
| 会话令牌 | 256-bit `secrets.token_urlsafe` + DB 过期 |
| 默认鉴权策略 | `RPG_DEPLOYMENT_MODE` 控制 + `RPG_REQUIRE_AUTH=1` 覆盖 |
| 启动 banner | 打印当前部署模式 + 是否强制鉴权 |
| 管理员鉴权 | `_require_api_user(admin=True)` 单独检查 role |
| 越权防护 | 所有 `/api/saves/{id}/*`、`/api/scripts/{id}/*` 调用 `_require_script` 检查 owner |

**已验证**：未登录访问 `/api/memories?save_id=6` → 401。

### 2.2 输入校验（A1, A4）

| 控制项 | 实现 |
|--------|------|
| 文件上传大小限制 | `MAX_SKILL_BYTES`、`MAX_ATTACHMENT_BYTES` |
| 路径遍历防护 | `library.safe_path` 限定在 `user_<id>/` 根目录内 |
| zip-slip 防护 | `_extract_skill_zip` 检查 `..` 和绝对路径 |
| 文件名清理 | `Path(name).name` 剥离目录组件 |
| MCP/Skill 写入权限 | 只允许管理员 |
| CORS Origin 白名单 | `_origins` 配置 + 中间件检查 |
| 恶意 Origin 自动 403 | mutating 请求二次检查 |

**已验证**：
- `POST /api/stop` with `Origin: https://evil.example` → 403。
- 路径遍历 `../../../etc/passwd` 在 `safe_path` 解析后落到 `user_<id>/etc/passwd`（被限制在用户目录）。

### 2.3 SQL 注入防护

**扫描结果**：所有 `cur.execute` / `db.execute` 调用使用参数化查询（`%s`、`?` 占位符）。

**剩余 f-string 拼接** 5 处，均为硬编码字段名/表名（不来自用户输入）：

```python
# platform_app/db.py:560-562
db.execute(f"alter table {table} add column ...")  # table 来自内部白名单
# timeline_index.py:87
cur.execute(f"ALTER TABLE vectors ADD COLUMN {col} {ddl}")  # col/ddl 来自固定字典
```

**建议**：未来如果引入用户可配置 schema，需改为白名单 + identifier 包裹。

### 2.4 命令注入与 RCE 防护

| 风险 | 控制 |
|------|------|
| `subprocess` 调用 | 全部不用 `shell=True`，传 list 而非字符串 |
| Skill 沙箱执行 | `skill_executor.py`：临时目录 + ulimit + 30s 超时 + 环境变量白名单 |
| MCP server 启动 | 仅管理员可配置 command，且需要登录 |

**Skill 沙箱已验证**：
- 超时杀进程（2s 超时实测 timeout=True）
- 环境变量隔离（`ANTHROPIC_API_KEY` 不可见）
- 工作目录隔离（脚本只能看到自己临时目录）
- macOS 上 `RLIMIT_AS` 对 Python 不生效（Python 内存管理特性），Linux 服务器正常

### 2.5 CSRF 防护

- mutating 请求（POST/PUT/PATCH/DELETE）经 `_origin_allowed` 检查 Origin
- 浏览器同源策略 + cookie SameSite（依赖部署时配置）

### 2.6 敏感信息保护

| 项 | 状态 |
|---|------|
| `.env` 文件 | 在 `.gitignore` 中 |
| `vertex_sa.json` | 在 `.gitignore` 中 |
| API Key 不入日志 | 启动 banner 只显示模式名，不显示 key |
| DB URL 暴露给前端 | `redacted_url` 屏蔽密码字段 |

---

## 3. 剩余风险与建议

### 3.1 已知风险（需运维侧处理）

| # | 风险 | 影响场景 | 缓解 |
|---|------|----------|------|
| R1 | macOS 上 Skill 沙箱内存限制不生效 | 多用户公网部署 | Linux + Docker/nsjail 二次隔离 |
| R2 | Skill 没有网络隔离 | 多用户公网部署 | 容器内 deny outbound 或 firejail |
| R3 | MCP server 的 stdio 进程没有资源限制 | 公网部署 | 后续给 `MCPServerConn` 加 `_preexec_setrlimit` |
| R4 | 没有请求速率限制 | 公网部署 | 前置 Nginx/Cloudflare rate-limiting |
| R5 | LLM API key 在内存中可被 dump | 高级威胁 | 短期 key + IAM 轮转 |
| R5a | vertex_sa.json 单 admin SA 共用（见下方"凭证模型"说明） | 公网 SaaS 多租户 | per-user GCP 凭证（P2 待实现） |
| R6 | Skill 上传无签名验证 | 多用户公网部署 | 引入 PGP 签名或仅允许 admin 上传 |
| R7 | Skill 执行结果无审计 | 多用户公网部署 | 把 `run_skill_command` 输出落库 |
| R8 | 会话 token 没有 refresh 机制 | 长会话不便 | 实现刷新 token + 短期 access token |

### 凭证模型: multi-tenant 注意事项

**单 admin 服务账号 (默认)**:
- `rpg/vertex_sa.json` 是 admin 级 Google Cloud Service Account
- 所有用户的 Vertex/Gemini LLM 调用共用此 SA
- 用户的 ANTHROPIC API key 经 AES-GCM 加密存 `user_api_credentials` (per-user 隔离)
- Anthropic / OpenAI-compat key 在 `user_api_credentials` 加密存储

**多租户部署若需 per-user GCP 凭证**:
- 这是 P2 设计选择 (单租户/家庭/小团队场景默认 OK)
- 真正 SaaS 部署需要:
  1. 关闭 `vertex_sa.json` env fallback (`resolve_api_key` 中 `RPG_DEPLOYMENT_MODE=server`)
  2. 让 user_api_credentials 支持 `provider="vertex"` + `payload=base64(json)`
  3. crypto_utils 已支持任意 bytes 加密，扩展 schema 即可

**当前 limitation**:
- 公开 SaaS 直接上线前必须做 per-user GCP 凭证隔离
- `rpg/SECURITY_AUDIT.md` 的 R5 "API key 在内存中可 dump" 风险在此放大

### 3.2 已知非问题

| 项 | 说明 |
|---|------|
| 默认本地模式不强制鉴权 | 设计选择，单用户本地工具的合理默认。`RPG_REQUIRE_AUTH=1` 可覆盖 |
| `runtime_states/` 在文件系统可读 | 本地模式不构成越权（同用户进程） |
| Skill 执行后保留 stdout | 由调用方决定是否记录 |

---

## 4. 攻击路径推演

### 4.1 路径 A：恶意 SKILL.zip 上传

```
攻击者 ──上传─▶ /api/skills/import (需 admin) ──>
  zip 解压到 user_skills/<id>/ ──>
  SKILL.md 验证 ──>
  注册到 imported_skills 表
```

**当前防护**：
- 需要 admin role
- zip-slip 检查
- 大小限制 `MAX_SKILL_BYTES`
- 解压目标限定在 `USER_SKILL_DIR` 子目录

**残留风险**：解压出的脚本如果被调用执行，靠 `skill_executor` 沙箱兜底。

### 4.2 路径 B：恶意 MCP server 配置

```
攻击者 ──设 command=─▶ /api/mcp/server (需 admin) ──>
  写入 mcp_servers 表 ──>
  ▶ 启动: /api/mcp/server/start ──> subprocess.Popen
```

**当前防护**：
- 需要 admin
- subprocess 不用 shell=True
- env 来自配置（不会泄露主进程 env）

**残留风险**：admin 本身就是危险角色。建议运维侧不要把 admin 给外部用户。

### 4.3 路径 C：越权读取其他用户存档

```
A2 ──GET /api/saves/{id}/context-runs (其他用户的 save_id)
```

**当前防护**：
- `knowledge.list_context_runs` 第一行就查 `where id = %s and user_id = %s`
- 不属于当前 user 直接 raise `无权访问该存档`

**已通过代码评审**。

### 4.4 路径 D：SQL 注入

```
攻击者 ──body 注入恶意值──▶ 任何 /api/* mutating 端点
```

**当前防护**：参数化查询全覆盖。

**已通过 grep 全文检查**，未发现动态拼接用户输入的 execute。

---

## 5. 已通过的渗透测试

1. ✅ 未登录访问 mutating 端点 → 401 / 关闭鉴权时降级（设计）
2. ✅ 恶意 Origin POST → 403
3. ✅ 越权 save_id 访问 → 抛错
4. ✅ Skill 沙箱：超时 / 不存在命令 / 环境隔离 / 目录隔离全部 OK
5. ✅ MCP broker：握手 / tools/list / tools/call / 关闭 全流程通

## 6. 推荐部署清单

### 6.1 本地单机（默认）

```bash
# 无需额外配置
../rpg_env/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7860
```

### 6.2 自托管（家人/小团队）

```bash
export RPG_DEPLOYMENT_MODE=self_hosted
export RPG_REQUIRE_AUTH=1
export RPG_CORS_ORIGINS="https://your.domain"
../rpg_env/bin/python -m uvicorn app:app --host 0.0.0.0 --port 7860
```

外加：
- Nginx 前置 + TLS
- cookie SameSite=Strict + Secure
- Postgres 单独账号 + 最小权限

### 6.3 公网部署（需严格运维）

在 6.2 基础上加：

```bash
export RPG_DEPLOYMENT_MODE=server
```

- 用 Docker 容器隔离（每个用户单独 namespace 更佳）
- Skill 执行用 nsjail/firejail 二次隔离
- Cloudflare/Nginx rate-limiting
- LLM API key 用 IAM 短期凭证轮转
- 关闭 Skill 上传（仅允许 admin）
- MCP server 仅白名单
- 监控 + 告警

---

## 7. 审计结论

**风险等级：本地/自托管使用 — 低**

代码安全态势良好：
- 鉴权/越权防护完整
- 参数化查询全覆盖
- 文件上传隔离 + 沙箱执行
- CSRF/CORS 双重防护
- 敏感信息 .gitignore

**风险等级：未加强的公网部署 — 中高**

不建议直接公开访问，必须配合：
- 容器隔离
- WAF
- 速率限制
- IAM 凭证轮转

**待后续工作**：
- Skill 沙箱在 Linux 上的 RLIMIT 行为真实回归测试
- MCP broker 子进程加资源限制
- 引入 schema_migrations 后增加 migration 完整性测试
- 引入 audit_log 表跟踪 admin 写入操作

---

附：本次审计的扫描命令

```bash
# SQL 注入扫描
grep -rn 'execute(f"\|\.execute("[^"]*" +' platform_app/*.py *.py

# Shell 注入扫描
grep -rn "shell=True" platform_app/*.py *.py

# 硬编码 secret 扫描
grep -nE "password\s*=\s*['\"]|api_key\s*=\s*['\"]" platform_app/*.py *.py

# 路径遍历扫描
grep -nE "Path\(.*body\.|Path\(.*request" platform_app/*.py *.py

# 实战渗透
curl -i -X POST http://127.0.0.1:7860/api/stop -H 'Origin: https://evil.example'  # → 403
curl -i http://127.0.0.1:7860/api/memories?save_id=6                              # → 401
```

---

# 威胁模型（Threat Model）

## 角色定义

| Actor | 描述 | 假设能力 |
|---|---|---|
| **A1 外部攻击者** | 未登录，可达 HTTP 端口 | 任意 HTTP request、源 IP 伪造、X-Forwarded-For 伪造 |
| **A2 普通用户** | 已注册登录，role=user | 自己的 cookie；本人凭证；常规 API |
| **A3 管理员** | role=admin | 全部 admin-only API；本地 vertex_sa.json 文件 |
| **A4 上游 LLM provider** | Anthropic/Vertex/OpenAI 后端 | 在响应里嵌入恶意 prompt（间接 prompt injection） |
| **A5 第三方 MCP server** | admin 配置的外部 stdio 进程 | 可读 chat 上下文；可写本地文件（如果 GM 让它写） |

## A1 外部攻击者

| 攻击 | 防御 | 残留 |
|---|---|---|
| 暴力撞库 | 同 IP+username 5 次错误锁 60s，登录审计写 DB | XFF 伪造已挡（trusted proxy 白名单） |
| CSRF mutating 请求 | Origin 白名单中间件 → 403 | 同源攻击（XSS 通道）→ 见 A2 |
| 信息收集 `/api/auth/me` | 未登录只露 `{driver, ok}`；DB version / user 仅 admin 见 | — |
| 信息收集 `/api/platform` | 服务器模式未登录 → 401 | — |
| 信息收集 `/api/platform/commands` | 服务器模式未登录 → 401 | — |
| SQL 注入 | 全部参数化查询 | — |
| 文件上传 XSS | download 强制 octet-stream + nosniff + CSP sandbox | — |
| 路径遍历 | `library.safe_path` 限定 user_<id>/ 根 | — |

## A2 普通用户

| 攻击 | 防御 | 残留 |
|---|---|---|
| 越权读他人存档 | game_saves 全部 where user_id 严格匹配；runtime.read_runtime per-user 文件 | — |
| 越权读他人 runtime checkout | runtime_checkouts.user_id 严格过滤；load_active_state 强校验 | — |
| 越权读 MCP secret | `/api/tools` / `/api/platform` / `/api/state` 都走 `_redact_*`，普通用户拿不到 command/args/env | — |
| 越权读 model credential | `/api/state.models` 脱 credential_env/ref/base_url；`/api/me/credentials` 不返 raw key | — |
| 盗用服务端 LLM 凭证 | `model_probe` 全链路按 user_credentials 取 key；服务器模式 Vertex 直接拒绝普通用户 | — |
| SSRF via base_url_override | 普通用户 `allow_base_url=False`（写入丢弃）；admin 也走 _validate_base_url 拒私网 | — |
| DoS via 重复 import 任务 | 同 (user,script) 去重；per-user 并发上限 1 | — |
| ReDoS via custom regex | 静态检查 + multiprocess timeout 探测 | macOS RLIMIT_AS 不生效（Linux 正常） |
| MCP 工具调用滥用 | 服务器模式仅 admin 可调 `/api/mcp/tool/call` | per-user MCP 尚未实现 |
| 跨用户 stop 信号 | `_stop_events_by_user` + DB stop_signals 表都按 user 隔离 | — |
| 上传 0 字节 / 畸形 base64 | validate=True 严格 + 显式拒绝空 | — |
| 上传超量文件静默截断 | MAX_ATTACHMENTS_PER_REQUEST 超量直接 raise | — |

## A3 管理员

| 攻击 | 防御 | 残留 |
|---|---|---|
| 配置恶意 MCP server command | admin 默认信任；外层应限制 admin 角色授予 | **设计权衡：admin 即超管** |
| 配置 SSRF base_url | `_validate_base_url` 拒私网；https 强制（服务器模式） | http 在本地匿名允许（合理） |
| 导入恶意 Skill 跑代码 | `skill_executor` 沙箱：临时目录 + ulimit + 30s timeout + 环境白名单 | macOS 内存限制不生效 |

## A4 上游 LLM provider（间接 prompt injection）

| 攻击 | 防御 | 残留 |
|---|---|---|
| LLM 在响应里嵌入 "/set 系统时间=..." | structured_updates 走 `permissions.mode`：default 弹确认、auto_review 审、full_access 直写。前端只在确认后才落库 | **full_access 模式下 LLM 可直接改 state** —— 这是用户授权的设计选择 |
| LLM 注入恶意 tool_call | 当前 MCP 工具循环未接入主 GM；接入后必须加 tool 白名单 | 待实现 |
| LLM 输出大量 token 烧钱 | token_usage 实时累加；超限可加预算阈值阻断 | 阈值阻断未实现 |

## A5 第三方 MCP server

| 攻击 | 防御 | 残留 |
|---|---|---|
| MCP server 读取 chat 上下文外泄 | MCP server 由 admin 自己配置；外部信任链是 admin → MCP | 没有 capability 隔离 |
| MCP server 在 stderr 喷 secret | 普通用户拿不到 `last_stderr`（admin only） | — |
| MCP server 崩溃 | broker stop_all on shutdown；未实现自动重启 | 待实现健康检查 |

## 部署模式建议

| 模式 | 适合 | 必须设置 |
|---|---|---|
| **local** | 单用户开发 | 默认即可 |
| **self_hosted** | 家庭/小团队 < 50 人 | `RPG_REQUIRE_AUTH=1`、TLS 前置 |
| **server** | 公网公开 | `RPG_REQUIRE_AUTH=1`、`RPG_DEPLOYMENT_MODE=server`、`RPG_TRUSTED_PROXIES=<nginx_ip>`、`RPG_MASTER_KEY=<32 字节 hex>`、容器隔离、WAF、速率限制 |

## 已知未做（不阻塞但要进 roadmap）

1. token_usage 阈值阻断（超用户/save 上限自动拒绝调用）
2. MCP 工具循环接入主 GM + tool 白名单
3. capability-based MCP server 沙箱（network deny 等）
4. audit_log 写库（admin 操作可追溯）
5. session token 刷新 / 主动失效
6. CSRF token（当前靠 SameSite cookie + Origin）
