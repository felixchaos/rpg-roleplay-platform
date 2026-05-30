# Changelog

All notable changes to RPG Roleplay are documented here.

Format adapted from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version scheme: `0.x-waveN[.M]` where `wave` matches the in-repo development cadence (`feat: Wave 14.2 — ...`).

---

## [Unreleased]

### Working towards
- Branches: merge / cleanup / deletion (currently stubs)
- Script-pack: sharing surface (import works, share UI in progress)
- Provider catalog: Qwen / Google AI Studio full `LlmBackend` impls (currently catalog-only)
- Web UI polish pass

---

## [0.1.0-wave14] — 2026-05-30

The Python → Rust migration is functionally complete. Wave 14 closed every
"not yet implemented" stub in the core game loop. Branches and script-pack
remain at "critical path only" status — see [docs/MIGRATION_AUDIT.md](./docs/MIGRATION_AUDIT.md) rows 5 and 6 for file:line specifics.

### Added
- Rust core game loop — state, ops, scenes, dice, D&D 5E core, encounters, inventory, retrieval, agents
- ts-rs typed frontend — 43 generated TypeScript types, vite proxy to axum
- 10-provider LLM catalog — 6 wired backends (Anthropic, OpenAI Responses, Vertex Gemini, OpenAI-compatible, OpenRouter, DeepSeek/xAI/MiMo/Hunyuan via shared backend), 4 catalog-only (Alibaba Qwen, Google AI Studio listed without backend impl yet)
- Postgres + pgvector storage — 24 versioned migrations, auto-apply on boot under advisory lock
- React 18 + Vite frontend — 3 page entries (Login / Platform / Game Console)
- Branch saves — commit / ref / checkout work like Git
- Script pack import — user-uploaded ZIPs with script + chapters + facts + cards
- `docs/MIGRATION_AUDIT.md` — file:line-level migration audit for AI assistants

### Changed
- LICENSE — MIT → Proprietary (AGPL-3.0 + commercial dual-license planned for v1 public release)
- README rewritten with honest "what works today" status, ASCII architecture diagram, provider matrix, "why not SillyTavern" positioning
- Hero subtitle — "一本小说扔进去，剧本就备好了" → "千人千面的剧本，从你自己的故事开始"

### Not yet
- Branches: merge / cleanup / deletion (`rust/crates/rpg-platform/src/branches/` — see audit row 5)
- Script-pack: sharing surface
- Public deployment + commercial license
- 2 providers without backend impl (Alibaba Qwen, Google AI Studio)

---

## Earlier waves (pre-changelog)

For history before 0.1.0, see `git log --oneline | grep -E '^[a-f0-9]+ (feat|fix|chore): Wave'` —
each wave commit message is the authoritative changelog entry for that wave.
Wave 1 through Wave 13.8 covered the initial Python skeleton, the Rust workspace
bootstrapping (Wave 6C onwards), and the parity audit (Wave 13.7 closed the
last 104 gaps between Python and Rust).
