# Changelog

All notable changes to RPG Roleplay are documented here.

Format adapted from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version scheme: `0.x-waveN[.M]` where `wave` matches the in-repo development cadence (`feat: Wave 14.2 — ...`).

---

## [Unreleased]

### Added
- User feedback drawer history: users can see their submitted feedback and review status, including "adopted" acknowledgements after fixes are verified.
- Admin feedback replies: administrators can answer feedback, and users can read those replies in their feedback history.

### Changed
- Model selection is now per-user/per-save for normal users, while global catalog changes remain admin-only.
- Custom API credential entry is limited to supported providers for non-admin users to avoid unusable model/provider combinations.
- Game Console mobile side panels now open as a full-width bottom sheet with larger touch targets and horizontally scrollable tabs.
- Main GM output now defaults to a 4K token BYOK budget, with higher user-configurable headroom, so story replies are not cut off by the old strict cap.

### Fixed
- Game Console now turns invalid or expired BYOK API keys into an actionable settings prompt instead of showing only a generic chat failure.
- Background phase summaries now use the save owner's model credentials, so long-memory compaction no longer falls back to an unconfigured server Vertex account.
- New-save player origin selection no longer forces an initial identity card; the identity overlay is now truly optional for all origin modes.
- Game Console openings now convert trailing markdown action lists into the GM choice box and refresh the streamed opening with the cleaned stored state.
- New-save identity recommendations now surface the backend's real failure reason when the LLM returns `ok:false`, instead of replacing it with a generic empty-result message.
- Opening messages are now recorded as branch commits, so forking from the first GM opening no longer checks out an empty root state.
- Game Console curator clarifications now only interrupt the GM when confidence is below the user's threshold, reducing unnecessary choice prompts when the story can continue.
- Script module rebuild progress is cleared when switching scripts, so an active extraction/rebuild banner from one script no longer appears on another script's detail view.
- Game Console curator clarification prompts now parse inline `(A)/(B)` options and refresh pending questions during streaming, so users see clickable choices instead of repeated plain-text questions.
- Script deletion from "My Scripts" now sends the confirmed force-delete flag so scripts with saves are actually removed together with their saves, matching the existing warning text.
- NPC character-card creation now lets users choose the target script in the add dialog, so adding from the "all scripts" view no longer appears blocked when a user has multiple scripts.
- Chunked `.txt` / `.md` script import now validates the uploaded filename instead of rejecting valid imports because of the display title.
- Tavern/SillyTavern character-card import now splits common structured profile sections into identity, appearance, background, personality, speech style, status, and secrets instead of putting the whole description into one field.
- Settings now clearly exposes the personal default main GM model selector, so users do not have to rediscover the model switcher each time.
- Game Console feedback drawer now uses the same dark Cloudscape theme as Platform, avoiding the bright default modal during gameplay.
- Game Console model switching now writes the selected model to the active save and shows the session model after refresh.
- Game Console now has a local Enter-key mode toggle so testers can choose between Enter-to-send and Enter-for-newline.
- Game Console now restores the player's draft when chat streaming fails, closes, times out, or finishes without any GM reply.
- Model parameter settings now reload saved values after refresh, persist NSFW mode/presets, and let the main GM honor each user's max output token setting.
- Chat usage records now include model finish reason and the applied output budget, making token-limit truncation visible in ops logs.
- Vertex/Agent Platform chats now return a recoverable user-facing error when the Service Account JSON is missing instead of failing the request with a backend 500.
- Script module rebuilds now expose the missing estimate endpoint and show actionable embedding credential prerequisites instead of surfacing "Method Not Allowed" when rebuilding vector indexes.
- NPC character-card editing and deletion in the card library now call the existing script card APIs.
- Saving an NPC character card with an existing name now updates the existing card instead of failing with a duplicate-name backend error.
- Script import jobs ending in `done_with_errors` now leave the "importing" state instead of blocking new imports.
- Acceptance retry state writes now include a valid trace id and no longer pass an unsupported context field.
- Game Console message deletion now starts from the selected message, so deleting a GM reply no longer removes the previous player line.

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
