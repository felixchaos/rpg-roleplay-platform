<div align="center">

# RPG Roleplay

**把一本小说装进可玩世界的自托管 LLM 角色扮演引擎.**

[![status](https://img.shields.io/badge/status-private%20beta-orange)](https://play.stellatrix.icu)
[![rust](https://img.shields.io/badge/rust-1.83%2B-orange)](#)
[![license](https://img.shields.io/badge/license-Proprietary-lightgrey)](./LICENSE)
[![waitlist](https://img.shields.io/badge/waitlist-open-success)](https://play.stellatrix.icu)

[落地页 / 公测预约](https://play.stellatrix.icu) · [English README](./README.md)

</div>

![RPG Roleplay — 实时游戏控制台](./docs/assets/hero.png)

---

## 这是什么

把一本长篇小说塞进一个目录, 下一次启动服务时, 它就是一个可玩的 RPG 世界. 最早是为了把一本 485 万字小说做成可玩的剧本, 现在泛化成任意作者或 GM 都能把自己的故事丢进去的运行时. 引擎负责无聊的部分 — 分支存档, 骰子, 场景, 长文检索, provider 路由, token 账单 — LLM 专心扮演, 你专心讲故事.

## 当前实际可用程度

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

```bash
git clone https://github.com/felixchaos/rpg-roleplay-platform.git
cd rpg-roleplay-platform

# 1. Postgres(pgvector)+ pgbouncer + redis
docker compose -f deploy/docker-compose.yml up -d postgres pgbouncer redis

# 2. 后端 — axum 跑在 :7860, 首次启动自动跑完 24 个迁移
cp deploy/.env.example .env   # 最低需要填 ANTHROPIC_API_KEY
cargo run -p rpg-server

# 3. 前端 — vite 跑在 :5173, /api 代理到 :7860
cd frontend && npm install && npm run dev

# 4. 打开登录页(这是多页 Vite 构建,不是 SPA)
open http://localhost:5173/Login.html
```

进 Login 注册账号, 然后跳到 `Platform.html`(剧本库 / 角色卡 / 设置) 或 `Game Console.html`(实际游戏画面).

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

私有仓库, 开发中, 暂不接受外部 PR. 公测后会按 [CONTRIBUTING.md](./CONTRIBUTING.md) 开放贡献. 现在可以提 issue 或在[落地页](https://play.stellatrix.icu)预约公测.

## 许可

闭源, 保留所有权利. 详见 [LICENSE](./LICENSE).

未来公开发布时计划采用双重授权 — AGPL-3.0 用于非商业 / 社区用途, 另有独立商业 license 用于闭源 / SaaS 部署. 在那之前仓库私有, 暂不开放外部使用.

授权咨询: <felixchaos@stellatrix.icu>

---

*最初是为了把一本 485 万字小说做成可玩的世界, 后来引擎超出了那本书.*
