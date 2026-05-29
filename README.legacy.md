# 我蕾穆丽娜不爱你 — RPG 角色扮演平台

> 通用 RPG 后端 + React 前端,**支持任意小说作为剧本载体**。
> 当前内置剧本《我蕾穆丽娜不爱你》(485 万字、1310 章),也支持导入任意剧本。

---

## ✨ 特性一览

| 特性 | 说明 |
|---|---|
| ⚙️ **通用底座** | 剧本规则 (建议/phase/角色/地点) 通过 JSON 数据驱动,代码零硬编码 |
| 🤖 **多 LLM Provider** | Anthropic Claude / Vertex AI (Gemini) / OpenAI 兼容 API 三选一 |
| 🧠 **AI 子代理生态** | GM / extractor / worldbook_agent / context_agent / phase_digest_agent / anchor_seed_agent / acceptance_verifier / timeline_narrative_guard |
| 📚 **大书支持** | 章节自动拆分、ChapterFact 索引、RAG 语义召回、pgvector |
| ⚔️ **RPG 规则引擎** | 5E-compatible dice / encounter / inventory / scene / HP / AC 守门 |
| 🌳 **存档树** | Git-like commit / ref / checkout,任意分支继续游戏 |
| 🔐 **三模式部署** | local / self_hosted / server,开发到生产无缝切换 |
| 🖥️ **全栈** | FastAPI 后端 + React + Vite 前端,59 个游戏 API + 82 个平台 API |

---

## 🚀 快速启动

### 前置要求

- Python 3.11+
- PostgreSQL 14+
- Node.js 18+ (前端用)
- 至少一个 LLM API Key:Anthropic / Google Vertex AI / OpenAI 兼容

### 1. 克隆 & 初始化

```bash
git clone --recurse-submodules <repo-url>
cd 我蕾穆丽娜不爱你
```

### 2. 后端安装

```bash
cd rpg
python3.11 -m venv ../rpg_env
../rpg_env/bin/pip install -e ".[dev]"
```

### 3. 数据库

```bash
# 建库
createdb rpg_platform

# 初始化表 (项目内置迁移脚本)
../rpg_env/bin/python -c "from platform_app.db.init import init_db; import asyncio; asyncio.run(init_db())"
```

### 4. 环境变量

在 `rpg/` 目录新建 `.env`:

```env
DATABASE_URL=postgresql://localhost/rpg_platform
ANTHROPIC_API_KEY=sk-ant-...        # 或 GOOGLE_CLOUD_PROJECT + VERTEX_LOCATION
RPG_DEPLOYMENT_MODE=local           # local | self_hosted | server
```

### 5. 启动

```bash
# 回到子项目根
cd ..
./scripts/dev.sh start
```

后端启动后访问:
- 🎮 游戏 API: `http://127.0.0.1:7860/docs` (Swagger)
- 📖 ReDoc: `http://127.0.0.1:7860/redoc`

### 6. 前端 (可选)

```bash
cd frontend
npm install
npm run dev
```

---

## 🏗️ 架构总览

```
我蕾穆丽娜不爱你/
├── rpg/                          后端 (FastAPI + Python)
│   ├── app.py                    FastAPI 入口
│   ├── routes/                   主游戏 API router (59 endpoint)
│   ├── platform_app/             平台服务
│   │   ├── api/                  82 个平台 endpoint (auth/scripts/saves/me/...)
│   │   ├── db/                   连接 / 初始化 / 迁移 / pgvector
│   │   ├── knowledge/            记忆 / 世界线 / 角色卡 (service+repo 分层)
│   │   └── branches/             存档树操作
│   ├── schemas/                  Pydantic 请求/响应模型
│   ├── agents/                   AI 子代理
│   │   └── gm/                   GameMaster + 3 LLM backend
│   ├── tools_dsl/                /set 命令 + 86 工具 + dispatcher
│   ├── state/                    GameState + 3 mixin
│   ├── context_engine/           上下文构建 (layers / loaders / formatters)
│   ├── rules_bridge/             规则引擎 bridge (combat / intent / consume / suggest)
│   ├── console_assistant/        主控台助手
│   ├── modules/                  数据驱动 RPG 模组
│   │   └── _script_overrides/    剧本专属 metadata (规则/角色/地点/phase)
│   ├── core/                     config / security / logging
│   ├── utils/                    通用工具 (crypto 等)
│   └── tests/                    73 个测试文件
└── frontend/                     React + Vite 前端 (git submodule)
```

### 剧本数据驱动原理

所有剧本特有信息都存在 `rpg/modules/_script_overrides/<key>.json`,代码读配置而不读硬编码字符串。换剧本只需换 JSON,后端代码零改动。

---

## 📖 API 文档

完整文档见 [`rpg/docs/README.md`](rpg/docs/README.md),摘要:

| 入口 | 说明 |
|---|---|
| `http://127.0.0.1:7860/docs` | Swagger UI 在线交互测试 |
| `http://127.0.0.1:7860/redoc` | ReDoc 优美阅读体验 |
| `rpg/docs/openapi.json` | 静态导出的 OpenAPI 3 schema (159 endpoints, 188 KB) |

重新生成静态 schema:

```bash
cd rpg/
../rpg_env/bin/python -m scripts.gen_openapi
```

---

## 🛠️ 开发指南

### 跑测试

```bash
cd rpg
../rpg_env/bin/python -m unittest discover -s tests -t .
```

当前基线: **754 pass / 30 fail (frontend submodule,不影响后端) / 0 error**

### Lint

```bash
ruff check rpg/
```

当前约 249 个 F401 警告 (re-export 故意保留),其余应保持干净。

### Type Check (WIP)

```bash
mypy rpg/
```

mypy 注解尚未全部补齐,目前仅供参考。

---

## 🚢 部署模式

| 模式 | 启动方式 | 说明 |
|---|---|---|
| `local` (默认) | `RPG_DEPLOYMENT_MODE=local` | 单用户本地,无鉴权 |
| `self_hosted` | `RPG_DEPLOYMENT_MODE=self_hosted RPG_REQUIRE_AUTH=1` | 个人服务器,启用 Token 鉴权 |
| `server` | 同上 + WAF + 速率限制 + 容器隔离 | 生产级多用户部署 |

详细安全配置见 `rpg/SECURITY_AUDIT.md`。

---

## 🤖 AI 子代理说明

| 代理 | 职责 |
|---|---|
| `gm` | 主 GameMaster,驱动剧情推进 |
| `extractor` | 从对话中提取结构化状态变更 |
| `context_agent` | 动态选取最相关上下文片段 |
| `worldbook_agent` | 世界书条目激活与召回 |
| `phase_digest_agent` | 阶段摘要与压缩 |
| `anchor_seed_agent` | 关键锚点种子生成 |
| `acceptance_verifier` | 验证玩家行动是否符合规则 |
| `timeline_narrative_guard` | 时间线叙事一致性守护 |

---

## 📋 主要 API 端点

启动后完整文档见 `/docs`,以下是常用端点速览:

| 分组 | 路径前缀 | 说明 |
|---|---|---|
| 游戏核心 | `POST /chat` | 玩家行动输入,返回 GM 响应 |
| 状态 | `GET /state` | 当前游戏状态快照 |
| 存档树 | `POST /branch/commit` | 提交存档 |
| 存档树 | `POST /branch/checkout` | 切换到历史分支 |
| 平台 | `POST /platform/auth/login` | 登录 |
| 平台 | `GET /platform/scripts` | 可用剧本列表 |
| 平台 | `GET /platform/saves` | 存档列表 |

---

## 🤝 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 📄 License

[MIT](LICENSE) © 2026 Felix Chaos
