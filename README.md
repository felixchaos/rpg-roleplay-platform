<div align="center">

# RPG Roleplay

**Self-hostable LLM RPG engine that turns a novel into a playable world.**

[![status](https://img.shields.io/badge/status-private%20beta-orange)](https://play.stellatrix.icu)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](#)
[![license](https://img.shields.io/badge/license-AGPL--3.0-blue)](./LICENSE)
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
> 🟡 = the code is there, rough edges remain.
> ❌ = planned but not built.

| Layer | Status |
|---|---|
| **Python core game loop** (state, ops, scenes, dice, D&D 5E core, encounters, inventory, retrieval, agents) | ✅ Stable |
| **LLM routing** (Anthropic native, OpenAI Responses, Vertex Gemini, OpenAI-compatible) | ✅ Stable, streaming + tool-use + multimodal |
| **Postgres + pgvector storage**, v39+ versioned migrations, auto-apply on boot under advisory lock | ✅ Stable |
| **Vite + React 18**, JSDoc type annotations, multi-page entries | ✅ Stable |
| **Branchable saves** — commit / ref / checkout work like Git, hard-delete with 30-day grace queue | ✅ Stable |
| **Script ingestion** — TXT / ZIP upload, 7 chapter splitters, auto-extract character cards + worldbook + timeline, vector index | ✅ Stable |
| **SillyTavern V2/V3 import** — character cards (PNG tEXt / JSON) + chat history (JSONL → new save) | ✅ Stable |
| **Provider catalog** — 10 providers (Anthropic / OpenAI / Vertex / Google AI Studio / DeepSeek / DashScope / Hunyuan / MiMo / xAI / OpenRouter), BYOK encrypted at-rest (AES-256-GCM HKDF per-user-per-api), live model sniffing | ✅ Stable |
| **i18n** — zh-CN + en, ~2000 keys, full UI coverage (settings / login / platform / game / admin) | ✅ Stable |
| **Help system** — in-app HelpDrawer with 27 module docs | ✅ Stable |
| **Compliance suite** — adult-content splash gate, AGPL legal banner, feedback channel with NSFW pre-moderation, AUP/DMCA/CSAM admin runbooks | ✅ Stable |
| **Auth + registration** — invite-code gate, email verification (Resend), Argon2id with rehash-on-login, forgot-password, two-step register | ✅ Stable |
| **Account lifecycle** — soft deactivate, request-delete (30-day grace), data export, hard-delete cron | ✅ Stable |
| **License** | ✅ AGPL-3.0-or-later (this repo) + commercial dual-license available — contact <legal@stellatrix.icu> |

## Quick start

```bash
git clone https://github.com/stellatrix-labs/rpg-roleplay.git
cd rpg-roleplay

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

> **Production deployment**: see `deploy/` for Docker / bare-metal templates.

## Architecture

```
┌─ browser ────────────────────────────────────────────────────┐
│ React 18 + Vite + JS (ESM multi-page)                        │
│ Login.html · Platform.html · Game Console.html               │
│ Cloudscape Design System · api-client.js · i18n              │
└────────────────────┬─────────────────────────────────────────┘
                     │
┌─ uvicorn :7860 ────▼─────────────────────────────────────────┐
│ FastAPI · Python 3.12 · async/asyncio.to_thread              │
│ ┌── platform_app/ ──┐ ┌── agents/ ──┐ ┌── tools_dsl/ ──┐   │
│ │ auth / saves /    │ │ gm/master + │ │ tool_registry + │   │
│ │ branches / cards/ │ │ context /   │ │ MCP / Skill     │   │
│ │ scripts / admin / │ │ extractor / │ │ executor        │   │
│ │ feedback / policy │ │ black_swan/ │ └─────────────────┘   │
│ └───────────────────┘ │ verifier    │                        │
│                        └─────────────┘                        │
│ ┌── state/ ──────────┐ ┌── retrieval ┐ ┌── knowledge/ ──┐   │
│ │ GameState +        │ │ BM25-lite + │ │ chapter_indexer │   │
│ │ op protocol        │ │ pgvector    │ │ embeddings      │   │
│ └────────────────────┘ └─────────────┘ └─────────────────┘   │
└────────┬─────────────────────────┬──────────────────────────┘
         │ psycopg                 │ httpx
         ▼                         ▼
┌────────────────────┐  ┌──────────────────────────────────────┐
│ pgbouncer:6432 +   │  │ LLM providers                        │
│ Postgres + pgvector│  │ Anthropic / OpenAI / Vertex /        │
│ v39+ migrations    │  │ DeepSeek / DashScope (Qwen) /        │
│                    │  │ Hunyuan / MiMo / xAI / OpenRouter    │
└────────────────────┘  └──────────────────────────────────────┘
         │
         ▼
┌────────────────────┐
│ Redis :6379         │
│ session/cache/      │
│ rate-limit          │
└────────────────────┘
```

FastAPI backend with ~30+ route modules / agents / state mixins, ~1k pytest cases.

## LLM providers

| Provider | Catalog | Streaming | Tool use | Multimodal | Extended thinking |
|---|---|---|---|---|---|
| Anthropic | ✅ | ✅ | ✅ | ✅ | ✅ |
| OpenAI (Responses) | ✅ | ✅ | ✅ | ✅ | — |
| Google Vertex (Gemini) | ✅ | ✅ | ✅ | ✅ | — |
| OpenRouter | ✅ | ✅ via OpenAI-compat | partial | — | — |
| DeepSeek | ✅ | ✅ via OpenAI-compat | partial | — | — |
| xAI (Grok) | ✅ | ✅ via OpenAI-compat | partial | — | — |
| MiMo (Xiaomi) | ✅ | ✅ via OpenAI-compat | partial | — | — |
| Hunyuan (Tencent) | ✅ | ✅ via OpenAI-compat | partial | — | — |
| DashScope (Qwen) | catalog only | — | — | — | — |
| Google AI Studio | catalog only | — | — | — | — |

Adding a provider = one entry in `rpg/config/model_catalog.json` + (if a new wire protocol) one backend in `rpg/agents/gm/backends/`. Everything else — picker, capability filtering, cost accounting — is automatic.

## Stack

`Python 3.12+` · `FastAPI` · `uvicorn` · `psycopg` · `pgvector` · `pgbouncer` · `Redis` · `React 18` · `Vite` · `Cloudscape Design System`

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
| Stack | Node, plain HTML/CSS | Python + FastAPI + pgvector + React |
| Tests | Mostly ad-hoc | ~1k pytest cases |

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

A full annotated example lives in `deploy/.env.example`.

## Project layout

```
.
├── rpg/                       # Backend (Python 3.12+)
│   ├── app.py                 # FastAPI · uvicorn :7860
│   ├── platform_app/          # auth / saves / branches / scripts / cards / admin
│   │   ├── api/               # FastAPI route modules
│   │   ├── db/migrations.py   # versioned migrations + auto-apply
│   │   ├── knowledge/         # chapter indexer / canon repo
│   │   ├── tavern_cards.py    # SillyTavern V2 PNG/JSON import
│   │   └── crypto.py          # AES-256-GCM HKDF per-user key
│   ├── agents/
│   │   ├── gm/master.py       # Main GM (streaming SSE)
│   │   ├── gm/backends/       # Anthropic / OpenAI / Vertex / OpenAI-compat
│   │   ├── context_agent.py
│   │   ├── extractor.py
│   │   ├── black_swan_agent.py
│   │   └── acceptance_verifier.py
│   ├── state/                 # GameState + op protocol
│   ├── tools_dsl/             # Tool registry + MCP broker
│   ├── retrieval.py           # BM25-lite + pgvector
│   ├── chat_pipeline.py       # Phase 0-4 orchestration
│   └── tests/                 # pytest cases
│
├── frontend/                  # React 18 + Vite (multi-page ESM)
│   ├── Login.html · Platform.html · Game Console.html
│   └── src/
│       ├── pages/             # settings/scripts/cards/saves/admin
│       ├── components/        # HelpDrawer/AdultSplash/FeedbackDrawer
│       ├── i18n/              # zh-CN + en
│       └── api-client.js
│
├── deploy/
│   ├── bare-metal/README.md   # Production bare-metal runbook
│   ├── test-server/           # Test environment templates
│   └── Dockerfile / docker-compose.yml
│
└── docs/                      # Architecture design docs
```

## Contributing

This is an open-source project — contributions welcome. For now, please file issues and follow the [landing page](https://play.stellatrix.icu) for the public release window.

## License

Licensed under the **GNU Affero General Public License v3.0 or later** (AGPL-3.0-or-later). See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).

**Why AGPL?** RPG Roleplay is a server-side application. AGPL ensures any operator running it as a public service must also make their modified source available to users — keeping the engine open even when used as a SaaS.

**Commercial / closed-source use** is available under a separate dual-license. Contact <legal@stellatrix.icu>.

---

*Originally written to host one 4.85 million-character novel as a playable world. The engine has since outgrown its first story.*
