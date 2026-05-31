<div align="center">

# RPG Roleplay

**把一本小说装进可玩世界的自托管 LLM 角色扮演引擎.**

[![status](https://img.shields.io/badge/status-private%20beta-orange)](https://play.stellatrix.icu)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](#)
[![license](https://img.shields.io/badge/license-AGPL--3.0-blue)](./LICENSE)
[![waitlist](https://img.shields.io/badge/waitlist-open-success)](https://play.stellatrix.icu)

[落地页 / 公测预约](https://play.stellatrix.icu) · [English README](./README.md)

</div>

![RPG Roleplay — 实时游戏控制台](./docs/assets/hero.png)

---

## 这是什么

**千人千面的剧本，从你自己的故事开始。**

RPG Roleplay 把一本长篇小说扔进一个自托管的 LLM 驱动的 RPG 运行时: 分支存档、原文检索、agent 驱动的场景, 以及骰子、provider 路由、token 账单、角色卡、世界书 — 这些无聊的脚手架全部就位. 最初为了把一本 485 万字小说做成可玩的世界, 现在任何作者或 GM 都能塞进自己的故事.

## 当前实际可用程度

> 下面这张表是真实状态，不是 marketing。
> ✅ = 跑通测试,作者本人在生产里用着。
> 🟡 = 代码在,毛刺还有 — 看 [docs/MIGRATION_AUDIT.md](./docs/MIGRATION_AUDIT.md) 拿 file:line 级清单。
> ❌ = 在 roadmap 上,还没做。

| 层 | 状态 |
|---|---|
| **Rust 核心游戏循环**(state, op, scene, 骰子, 5E 核心, 遭遇, 物品栏, 检索, agents) | ✅ 稳定 |
| **LLM 路由**(Anthropic 原生 / OpenAI Responses / Vertex Gemini / OpenAI 兼容) | ✅ 稳定 — 流式 + 工具调用 + 多模态 |
| **Postgres + pgvector** 存储, 24 个版本化迁移, 启动时自动加咨询锁顺序执行 | ✅ 稳定 |
| **ts-rs 端到端类型** — 43 个 Rust 类型自动桥接到 TypeScript, Vite 代理至 axum | ✅ 稳定 |
| **可分支存档** — commit / ref / checkout 像 Git 一样工作 | 🟡 关键路径已通, merge / 清理 / 删除仍是骨架 |
| **剧本包** — 用户上传 ZIP 含 script + chapters + facts + cards | 🟡 导入可用, 共享面在做 |
| **Provider 目录** — 列了 10 家, 能力 metadata 已暴露给 UI | 🟡 6 家接了真后端, 4 家暂时只在目录里 |
| **Web UI** — 类型化 React 客户端, 3 个页面入口(Login / Platform / Game Console) | 🟡 核心循环 feature complete, 视觉打磨进行中 |
| **公开部署 / 商业 license** | ❌ 还没 — 见[公测预约](https://play.stellatrix.icu) |

## 快速开始

> **技术栈说明**: 后端是 Python / FastAPI / uvicorn（非 Rust）。下方架构图为历史/规划状态，当前可运行代码在 `rpg/` 目录下。

```bash
git clone https://github.com/felixchaos/rpg-roleplay-platform.git
cd rpg-roleplay-platform

# 1. 装 Postgres + pgvector（macOS；Ubuntu 改用 apt install postgresql-16 postgresql-16-pgvector）
brew install postgresql pgvector
brew services start postgresql

# 2. 创 rpg 用户 + 库
psql postgres -c "CREATE USER rpg WITH PASSWORD 'rpg_dev';"
psql postgres -c "CREATE DATABASE rpg OWNER rpg;"
psql -U rpg -d rpg -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -U rpg -d rpg -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 3. 装 Python 依赖
#    !! 重要：在 rpg/ 子目录内运行，不是仓库根 !!
cd rpg/
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. 配 .env
#    若 rpg/.env.example 不存在，从 deploy/test-server/.env.example 复制
cp .env.example .env   # 或: cp ../deploy/test-server/.env.example .env
$EDITOR .env           # 填 DATABASE_URL、RPG_MASTER_KEY、RESEND_API_KEY 等

# 5. 首次跑 migration（fresh DB 必须用 full，不能用 up）
#    !! 必须在 rpg/ 目录下运行（模块查找依赖工作目录）!!
.venv/bin/python -m platform_app.migrate full

# 6. 起后端
.venv/bin/uvicorn app:app --port 7860 --reload   # 开发模式
# 或一键起全栈（postgres + backend + frontend）:
# cd .. && ./scripts/dev.sh start

# 7. 起前端（另开终端）
cd ../frontend && npm install && npm run dev

# 8. 打开登录页（这是多页 Vite 构建，不是 SPA）
open http://localhost:5173/Login.html
```

进 Login 注册账号, 然后跳到 `Platform.html`（剧本库 / 角色卡 / 设置）或 `Game Console.html`（实际游戏画面）。

> **生产部署**: 完整裸机 runbook（systemd、PgBouncer 接法、migration 陷阱、本地数据红线）见 [deploy/bare-metal/README.md](./deploy/bare-metal/README.md)。

## 架构

```
                 ┌────────────────────────── 浏览器 ──────────────────────────┐
                 │ React 18 + Vite + TypeScript                              │
                 │ Login.html · Platform.html · Game Console.html            │
                 │ 43 个 ts-rs 类型 · 手写 api-client · SSE/WS 桥接           │
                 └────────────────────────┬──────────────────────────────────┘
                                          │ /api → 7860
                                          ▼
                 ┌────────────────────────── axum (:7860) ───────────────────┐
                 │ 27 个路由模块 · 单一 AppState · governor 限流 + body 上限 │
                 │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────┐ │
                 │ │ rpg-platform│ │  rpg-agents │ │  rpg-llm    │ │rpg-rules│ │
                 │ │ auth/saves/ │ │ GM + 9 个   │ │ router +    │ │ D&D 5E  │ │
                 │ │ branches/   │ │ 子 agent    │ │ 4 个后端 +  │ │ + JSON  │ │
                 │ │ runtime     │ │             │ │ 成本登记表  │ │ 模块    │ │
                 │ └─────────────┘ └─────────────┘ └─────────────┘ └────────┘ │
                 │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────┐ │
                 │ │  rpg-state  │ │ rpg-context │ │rpg-retrieval│ │rpg-tools│ │
                 │ │GameState +  │ │ 可插拔      │ │ BM25-lite + │ │MCP +   │ │
                 │ │ op 协议     │ │ providers   │ │ pgvector    │ │ skill  │ │
                 │ └─────────────┘ └─────────────┘ └─────────────┘ └────────┘ │
                 └────────────┬────────────────────────┬────────────────────┘
                              │ sqlx                   │ http
                              ▼                        ▼
                  ┌───────────────────────┐  ┌──────────────────────────────┐
                  │ pgbouncer (:6432) +   │  │  LLM 厂家                    │
                  │ Postgres + pgvector   │  │  Anthropic · OpenAI · Vertex │
                  │ 24 个迁移             │  │  + 6 个 OpenAI 兼容后端     │
                  └───────────────────────┘  └──────────────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │  Redis (:6379)        │
                  │  限流 · 缓存          │
                  └───────────────────────┘
```

15 个 Rust crate, 约 7.2 万行代码, 552 个 `#[test]` / `#[tokio::test]`.

## LLM 厂家

| 厂家 | 已列入目录 | 流式 | 工具调用 | 多模态 | 扩展思考 |
|---|---|---|---|---|---|
| Anthropic | ✅ | ✅ | ✅ | ✅ | ✅ |
| OpenAI (Responses) | ✅ | ✅ | ✅ | ✅ | — |
| Google Vertex (Gemini) | ✅ | ✅ | ✅ | ✅ | — |
| OpenRouter | ✅ | ✅(OpenAI 兼容) | 部分 | — | — |
| DeepSeek | ✅ | ✅(OpenAI 兼容) | 部分 | — | — |
| xAI | ✅ | ✅(OpenAI 兼容) | 部分 | — | — |
| 小米 MiMo | ✅ | ✅(OpenAI 兼容) | 部分 | — | — |
| 腾讯混元 | ✅ | ✅(OpenAI 兼容) | 部分 | — | — |
| 阿里 Qwen | 仅目录 | — | — | — | — |
| Google AI Studio | 仅目录 | — | — | — | — |

加一家 provider = `model_catalog/src/providers/` 里多一个文件 +(若是新协议)`rpg-llm` 里多一个 `LlmBackend` 实现. 选模型 / 能力过滤 / token 计费这些都是自动的.

## 技术栈

`Rust 1.83+` · `axum` · `sqlx` · `pgvector` · `pgbouncer` · `Redis` · `tokio` · `tower-governor` · `ts-rs` · `React 18` · `Vite` · `TypeScript`

## 为什么不是 SillyTavern / Risu / KoboldCpp?

我们很喜欢 SillyTavern. 它是一个出色的角色卡聊天工具. 但它和我们解决的是不同问题:

- **SillyTavern** = *"我有一张角色卡,让我跟它聊天."*
- **RPG Roleplay** = *"我有一本百万字小说,让我**走进里面玩一遍**."*

| 关注点 | SillyTavern / Risu | RPG Roleplay |
|---|---|---|
| 基本单位 | 角色卡 | 小说 + 设定集 |
| 长文检索 | 要扩展才有 | 内置 BM25 + pgvector 跑原文 |
| 分支存档 | 手动导出聊天记录 | Git 式 commit / ref / checkout |
| 引擎状态 | 对话历史 | 类型化 `GameState` + op 协议 + D&D 5E 核心 |
| 世界书 | YAML / JSON 文件 | 数据库条目 + 语义激活 |
| 多用户 | 单机应用 | 鉴权 + 用户级 runtime + 配额 |
| 技术栈 | Node + 原生 HTML/CSS | Rust + axum + sqlx + pgvector + 类型化 React |
| 测试 | 多为临时 | 15 个 crate 累计 552 个 `#[test]` |

故事是一个角色 → 用 SillyTavern。故事是一整个**世界** → 用 RPG Roleplay。两边都吃同一份 V2 卡格式,横移成本几乎为零.

## 配置

| 变量 | 用途 | 必填 |
|---|---|---|
| `DATABASE_URL` | Postgres 连接串(走 pgbouncer) | ✅ |
| `ANTHROPIC_API_KEY` | 默认 LLM provider, 首次跑起来必须有 | ✅ 首次 |
| `EMBED_BASE_URL` / `EMBED_MODEL` / `EMBED_API_KEY` | 检索用 embedding 模型 | ✅ |
| `REDIS_URL` | 限流 + 缓存后端 | ✅ |
| `RPG_CORS_ORIGINS` | 逗号分隔的允许 origin | ✅ 生产 |
| `RPG_PORT` / `RPG_HOST` | 改默认 `0.0.0.0:7860` | 可选 |
| `RPG_RATE_LIMIT_PER_MIN` | 按 IP 的 token bucket | 可选 |
| `RPG_REQUEST_TIMEOUT_SECS` | 非流式响应超时 | 可选 |
| `RPG_SKIP_AUTO_MIGRATE=1` | 跳过启动时自动迁移 | 可选 |
| `RUST_LOG` | 日志层级 `info,rpg_server=debug,sqlx=warn` 之类 | 可选 |

完整带注释的样例在 `deploy/.env.example`.

## 工程结构

```
.
├── rust/                        # 后端 workspace, 15 个 crate
│   └── crates/
│       ├── rpg-server/          # 二进制入口, axum 跑在 :7860
│       ├── rpg-routes/          # 27 个路由模块
│       ├── rpg-platform/        # 鉴权 · 存档 · 分支 · runtime · script-pack
│       ├── rpg-agents/          # GM + 9 个子 agent
│       ├── rpg-llm/             # 4 个后端 + LlmRouter + 成本登记表
│       ├── rpg-state/           # GameState + op 协议
│       ├── rpg-rules/           # D&D 5E 核心 + JSON 模块加载器
│       ├── rpg-context/         # 可插拔 context provider
│       ├── rpg-retrieval/       # BM25-lite + pgvector
│       ├── rpg-db/              # sqlx + 24 个 sql 迁移
│       ├── rpg-schemas/         # ts-rs 领域类型
│       ├── rpg-tools-dsl/       # 工具登记表 + MCP broker
│       └── model_catalog/       # 10 家 provider, 能力 metadata
│
├── frontend/                    # React 18 + Vite, 3 个 HTML 入口
│   ├── Login.html · Platform.html · Game Console.html
│   └── src/types/rust/          # 43 个 ts-rs 生成类型
│
├── deploy/                      # Dockerfile · docker-compose · k8s
└── rpg/modules/ash_mine/        # 唯一一个 ship 出去的示例剧本
```

## 贡献

私有仓库, 开发中, 暂不接受外部 PR. 公测后会按 [CONTRIBUTING.md](./CONTRIBUTING.md) 开放贡献. 现在可以提 issue 或在[落地页](https://play.stellatrix.icu)预约公测. 每个 Wave 的发布记录见 [CHANGELOG.md](./CHANGELOG.md).

## 许可证

本项目采用 **GNU Affero General Public License v3.0 或更新版本**(AGPL-3.0-or-later)。详见 [LICENSE](./LICENSE) 和 [NOTICE](./NOTICE)。

**为什么 AGPL?** RPG Roleplay 是服务端应用。AGPL 确保任何把它作为公开服务运营的人,必须开放其修改后的源代码给用户 — 即使作为 SaaS 使用,引擎也保持开放。

**商用 / 闭源** 可通过单独的双授权协议获取。联系 <legal@stellatrix.icu>。

---

*最初是为了把一本 485 万字小说做成可玩的世界, 后来引擎超出了那本书.*
