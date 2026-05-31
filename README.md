<div align="center">

# RPG Roleplay

**Self-hostable LLM RPG engine that turns a novel into a playable world.**

[![status](https://img.shields.io/badge/status-private%20beta-orange)](https://play.stellatrix.icu)
[![rust](https://img.shields.io/badge/rust-1.83%2B-orange)](#)
[![license](https://img.shields.io/badge/license-Proprietary-lightgrey)](./LICENSE)
[![waitlist](https://img.shields.io/badge/waitlist-open-success)](https://play.stellatrix.icu)

[Landing & waitlist](https://play.stellatrix.icu) · [中文 README](./README.zh-CN.md)

</div>

![RPG Roleplay — live game console](./docs/assets/hero.png)

---

## What it is

**Every reader who plays your story plays a different one.**

RPG Roleplay drops a long-form novel into a self-hosted, LLM-driven RPG runtime: branching saves, retrieval over the original text, agent-driven scenes, and all the boring scaffolding — dice, provider routing, token accounting, cards, worldbook — is already wired up. Originally written to host one 4.85-million-character novel as a playable world; now any author or GM can point it at their own.

## What works today

> The table below is the actual state, not marketing.
> ✅ = tests pass and the feature is used in production by the author.
> 🟡 = the code is there, rough edges remain — see [docs/MIGRATION_AUDIT.md](./docs/MIGRATION_AUDIT.md) for file:line specifics.
> ❌ = planned but not built.

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

> **Stack note**: the backend is Python / FastAPI / uvicorn (not Rust). The architecture diagram below is aspirational/legacy — the live codebase lives in `rpg/`.

```bash
git clone https://github.com/felixchaos/rpg-roleplay-platform.git
cd rpg-roleplay-platform

# 1. Install Postgres + pgvector (macOS example; Ubuntu: apt install postgresql-16 postgresql-16-pgvector)
brew install postgresql pgvector
brew services start postgresql

# 2. Create rpg user + database
psql postgres -c "CREATE USER rpg WITH PASSWORD 'rpg_dev';"
psql postgres -c "CREATE DATABASE rpg OWNER rpg;"
psql -U rpg -d rpg -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -U rpg -d rpg -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 3. Install Python dependencies
#    !! IMPORTANT: run from rpg/ sub-directory, not the repo root !!
cd rpg/
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Configure .env
#    No rpg/.env.example yet? Copy from deploy/test-server/.env.example
cp .env.example .env   # or: cp ../deploy/test-server/.env.example .env
$EDITOR .env           # set DATABASE_URL, RPG_MASTER_KEY, RESEND_API_KEY etc.

# 5. Run migrations — fresh DB requires "full", not "up"
#    !! Must run from rpg/ directory (module resolution depends on cwd) !!
.venv/bin/python -m platform_app.migrate full

# 6. Start the backend
.venv/bin/uvicorn app:app --port 7860 --reload   # dev
# Or use the one-shot script (starts postgres + backend + frontend):
# cd .. && ./scripts/dev.sh start

# 7. Start the frontend (separate terminal)
cd ../frontend && npm install && npm run dev

# 8. Open the login page (multi-page Vite build, not a SPA)
open http://localhost:5173/Login.html
```

You'll land on the Login page, create a user, then bounce to `Platform.html` (library + cards + scripts) or `Game Console.html` (the actual gameplay screen).

> **Production deployment**: see [deploy/bare-metal/README.md](./deploy/bare-metal/README.md) for a complete bare-metal runbook (systemd, PgBouncer wiring, migration pitfalls, data red-lines).

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

## Why not SillyTavern / Risu / KoboldCpp?

We love SillyTavern. It's an incredible character-card playground. But it answers a different question:

- **SillyTavern** = *"I have a character card. Let me chat with it."*
- **RPG Roleplay** = *"I have a million-character novel. Let me play **inside** it."*

| Concern | SillyTavern / Risu | RPG Roleplay |
|---|---|---|
| Primary unit | Character card | Novel + setting bible |
| Long-form retrieval | Extension required | Built-in: BM25 + pgvector over the original text |
| Branching saves | Manual chat export | Git-style commit / ref / checkout |
| Engine state | Conversation history | Typed `GameState` + op protocol + D&D 5E core |
| Worldbook | YAML / JSON files | DB-backed entries with semantic activation |
| Multi-user | Single-user app | Auth + per-user runtime + quota |
| Stack | Node, plain HTML/CSS | Rust + axum + sqlx + pgvector + typed React |
| Tests | Mostly ad-hoc | 552 `#[test]` annotations across 15 crates |

Use SillyTavern when your story is a character. Use RPG Roleplay when your story is a *world*. The two import the same V2 card format, so moving sideways is trivial.

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

This is a private repository in active development; external PRs aren't accepted yet. Once we ship public beta, contributions will be welcome under [CONTRIBUTING.md](./CONTRIBUTING.md). For now: file issues, follow the [landing page](https://play.stellatrix.icu) for the public release window, and see [CHANGELOG.md](./CHANGELOG.md) for what's shipped per wave.

## License

Proprietary. All rights reserved. See [LICENSE](./LICENSE).

A future public release is planned under a dual-license model — AGPL-3.0 for non-commercial / community use, plus a separate commercial license for closed-source or SaaS deployments. Until that release, this repository is private and not currently accepting external use.

For licensing inquiries: <felixchaos@stellatrix.icu>

---

*Originally written to host one 4.85 million-character novel as a playable world. The engine has since outgrown its first story.*
