<div align="center">

# RPG Roleplay

**Self-hostable LLM RPG engine that turns a novel into a playable world.**

[![status](https://img.shields.io/badge/status-private%20beta-orange)](https://play.stellatrix.icu)
[![rust](https://img.shields.io/badge/rust-1.83%2B-orange)](#)
[![license](https://img.shields.io/badge/license-Proprietary-lightgrey)](./LICENSE)
[![waitlist](https://img.shields.io/badge/waitlist-open-success)](https://play.stellatrix.icu)

[Landing & waitlist](https://play.stellatrix.icu) · [中文 README](./README.zh-CN.md)

</div>

---

## What it is

Drop a long-form novel into a directory; get a playable RPG world the next time the server boots. Originally written to host one specific 4.85-million-character novel as a game, now generalized into a runtime that any author or GM can point at their own story. The engine handles the boring parts — branching saves, dice, scenes, retrieval over long-form lore, provider routing, token accounting — so the LLM can focus on roleplay and you can focus on the story.

## What works today

| Layer | Status |
|---|---|
| **Rust core game loop** (state, ops, scenes, dice, D&D 5E core, encounters, inventory, retrieval, agents) | ✅ Stable |
| **LLM routing** (Anthropic native, OpenAI Responses, Vertex Gemini, OpenAI-compatible) | ✅ Stable, streaming + tool-use + multimodal |
| **Postgres + pgvector storage**, 24 versioned migrations, auto-apply on boot under advisory lock | ✅ Stable |
| **ts-rs typed frontend** — 43 types bridged Rust → TypeScript, vite proxy to axum | ✅ Stable |
| **Branchable saves** — commit / ref / checkout work like Git | 🟡 Critical path only; merge / cleanup / deletion are stubs |
| **Script pack** — user-uploaded ZIPs with script + chapters + facts + cards | 🟡 Import works, sharing surface in progress |
| **Provider catalog** — 10 providers listed, capability metadata exposed to UI | 🟡 6 wired to a real backend, 4 catalog-only for now |
| **Web UI** — typed React client, 3 page entries (Login / Platform / Game Console) | 🟡 Feature-complete for core loop, polish ongoing |
| **Public deployment / commercial license** | ❌ Not yet — see [waitlist](https://play.stellatrix.icu) |

## Quick start

```bash
git clone https://github.com/felixchaos/rpg-roleplay-platform.git
cd rpg-roleplay-platform

# 1. Postgres (pgvector) + pgbouncer + redis
docker compose -f deploy/docker-compose.yml up -d postgres pgbouncer redis

# 2. Backend — axum on :7860, runs 24 migrations on first boot
cp deploy/.env.example .env   # fill ANTHROPIC_API_KEY at minimum
cargo run -p rpg-server

# 3. Frontend — vite on :5173, proxies /api → :7860
cd frontend && npm install && npm run dev

# 4. Open the login page (it's a multi-page Vite build, not a single SPA)
open http://localhost:5173/Login.html
```

You'll land on the Login page, create a user, then bounce to `Platform.html` (library + cards + scripts) or `Game Console.html` (the actual gameplay screen).

## Architecture

```
                 ┌────────────────────────── browser ──────────────────────────┐
                 │ React 18 + Vite + TypeScript                                │
                 │ Login.html · Platform.html · Game Console.html              │
                 │ 43 ts-rs types · hand-rolled api-client · SSE/WS bridge     │
                 └────────────────────────┬────────────────────────────────────┘
                                          │ /api → 7860
                                          ▼
                 ┌────────────────────────── axum (:7860) ─────────────────────┐
                 │ 27 route modules · single AppState · governor + body limit  │
                 │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────┐ │
                 │ │ rpg-platform│ │  rpg-agents │ │  rpg-llm    │ │rpg-rules│ │
                 │ │ auth/saves/ │ │ GM + 9 sub- │ │ router +    │ │ D&D 5E  │ │
                 │ │ branches/   │ │ agents      │ │ 4 backends  │ │ + JSON  │ │
                 │ │ runtime     │ │             │ │ + cost reg  │ │ modules │ │
                 │ └─────────────┘ └─────────────┘ └─────────────┘ └─────────┘ │
                 │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────┐ │
                 │ │  rpg-state  │ │ rpg-context │ │rpg-retrieval│ │rpg-tools│ │
                 │ │GameState +  │ │ pluggable   │ │ BM25-lite + │ │MCP +    │ │
                 │ │ op protocol │ │ providers   │ │ pgvector    │ │skill exe│ │
                 │ └─────────────┘ └─────────────┘ └─────────────┘ └─────────┘ │
                 └────────────┬────────────────────────┬──────────────────────┘
                              │ sqlx                   │ http
                              ▼                        ▼
                  ┌───────────────────────┐  ┌──────────────────────────────┐
                  │ pgbouncer (:6432) +   │  │  LLM providers               │
                  │ Postgres + pgvector   │  │  Anthropic · OpenAI · Vertex │
                  │ 24 migrations         │  │  + 6 OpenAI-compat backends  │
                  └───────────────────────┘  └──────────────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │  Redis (:6379)        │
                  │  rate-limit · cache   │
                  └───────────────────────┘
```

15 Rust crates, ~72k LoC, 552 `#[test]` annotations.

## LLM providers

| Provider | Catalog | Streaming | Tool use | Multimodal | Extended thinking |
|---|---|---|---|---|---|
| Anthropic | ✅ | ✅ | ✅ | ✅ | ✅ |
| OpenAI (Responses) | ✅ | ✅ | ✅ | ✅ | — |
| Google Vertex (Gemini) | ✅ | ✅ | ✅ | ✅ | — |
| OpenRouter | ✅ | ✅ via OpenAI-compat | partial | — | — |
| DeepSeek | ✅ | ✅ via OpenAI-compat | partial | — | — |
| xAI | ✅ | ✅ via OpenAI-compat | partial | — | — |
| Xiaomi MiMo | ✅ | ✅ via OpenAI-compat | partial | — | — |
| Tencent Hunyuan | ✅ | ✅ via OpenAI-compat | partial | — | — |
| Alibaba Qwen | catalog only | — | — | — | — |
| Google AI Studio | catalog only | — | — | — | — |

Adding a provider = one entry in `model_catalog/src/providers/` + (if a new wire protocol) one `LlmBackend` impl in `rpg-llm`. Everything else — picker, capability filtering, cost accounting — is automatic.

## Stack

`Rust 1.83+` · `axum` · `sqlx` · `pgvector` · `pgbouncer` · `Redis` · `tokio` · `tower-governor` · `ts-rs` · `React 18` · `Vite` · `TypeScript`

## Configuration

| Variable | Purpose | Required |
|---|---|---|
| `DATABASE_URL` | Postgres connection string (via pgbouncer) | ✅ |
| `ANTHROPIC_API_KEY` | Default LLM provider — needed for first-run | ✅ at first |
| `EMBED_BASE_URL` / `EMBED_MODEL` / `EMBED_API_KEY` | Embedding model for retrieval | ✅ |
| `REDIS_URL` | Rate-limit + cache backend | ✅ |
| `RPG_CORS_ORIGINS` | Comma-separated allowed origins | ✅ in prod |
| `RPG_PORT` / `RPG_HOST` | Override default `0.0.0.0:7860` | optional |
| `RPG_RATE_LIMIT_PER_MIN` | Per-IP token bucket | optional |
| `RPG_REQUEST_TIMEOUT_SECS` | Non-streaming response timeout | optional |
| `RPG_SKIP_AUTO_MIGRATE=1` | Skip the boot-time migration runner | optional |
| `RUST_LOG` | `info,rpg_server=debug,sqlx=warn` etc. | optional |

A full annotated example lives in `deploy/.env.example`.

## Project layout

```
.
├── rust/                        # Backend workspace, 15 crates
│   └── crates/
│       ├── rpg-server/          # Binary, boots axum on :7860
│       ├── rpg-routes/          # 27 route modules
│       ├── rpg-platform/        # Auth · saves · branches · runtime · script-pack
│       ├── rpg-agents/          # GM + 9 sub-agents
│       ├── rpg-llm/             # 4 backends + LlmRouter + cost registry
│       ├── rpg-state/           # GameState + op protocol
│       ├── rpg-rules/           # D&D 5E core + JSON module loader
│       ├── rpg-context/         # Pluggable context providers
│       ├── rpg-retrieval/       # BM25-lite + pgvector
│       ├── rpg-db/              # sqlx + 24 sql migrations
│       ├── rpg-schemas/         # ts-rs domain types
│       ├── rpg-tools-dsl/       # Tool registry + MCP broker
│       └── model_catalog/       # 10 providers, capability metadata
│
├── frontend/                    # React 18 + Vite, 3 HTML entries
│   ├── Login.html · Platform.html · Game Console.html
│   └── src/types/rust/          # 43 ts-rs generated types
│
├── deploy/                      # Dockerfile · docker-compose · k8s
└── rpg/modules/ash_mine/        # Sole shipped example scenario
```

## Contributing

This is a private repository in active development; external PRs aren't accepted yet. Once we ship public beta, contributions will be welcome under [CONTRIBUTING.md](./CONTRIBUTING.md). For now: file issues, follow the [landing page](https://play.stellatrix.icu) for the public release window.

## License

Proprietary. All rights reserved. See [LICENSE](./LICENSE).

A future public release is planned under a dual-license model — AGPL-3.0 for non-commercial / community use, plus a separate commercial license for closed-source or SaaS deployments. Until that release, this repository is private and not currently accepting external use.

For licensing inquiries: <felixchaos@stellatrix.icu>

---

*Originally written to host one 4.85 million-character novel as a playable world. The engine has since outgrown its first story.*
