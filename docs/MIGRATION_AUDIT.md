# RPG Roleplay Platform — Migration Audit

Audit performed against `main` at commit `2e2d3326` (post-Wave 14.3). The codebase is the result of a Python → Rust rewrite that culminated in Wave 14 ("stub 清零, 全部 0 个 'not yet implemented'"). Several module-header docstrings still describe "骨架/skeleton" status that no longer matches the code body — when in doubt, **read the function, not the module header**.

Workspace: 15 crates, 24 migrations, ~552 `#[test]` annotations, 43 ts-rs types. All paths below are relative to repo root `/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/`.

---

## Row 1: Rust core game loop (✅)

### What's implemented (evidence)

State (`rpg-state`, ~5.6k LoC):
- `rust/crates/rpg-state/src/lib.rs` — crate root, lists submodules and design.
- `rust/crates/rpg-state/src/state.rs:1-560` — `GameState` typed wrapper around `rpg_schemas::GameStateData`, version/touch, Arc-snapshot cache, `CURRENT_SCHEMA_VERSION = 6`.
- `rust/crates/rpg-state/src/typed_path.rs` (560 lines) — JSON-Pointer style dispatch into the typed top-level fields. **Critical**: this replaces the old `data: Value + ensure_object_at` pattern. 15 tests inline.
- `rust/crates/rpg-state/src/ops.rs:1-12` (header) — `Op` enum (`Set / Append / Inc / Merge / Delete`) plus 5-gate `apply_op`: hard-forbidden → rules-managed → module-managed → permission-mode → pass. 12 tests inline.
- `rust/crates/rpg-state/src/path.rs` — dot/bracket parse + Chinese-alias normalization.
- `rust/crates/rpg-state/src/store.rs` — `StateStore = Arc<DashMap<String, Arc<RwLock<GameState>>>>` (key is `String` not `UserId` so anonymous `"anonymous"` sentinel fits).
- `rust/crates/rpg-state/src/bus.rs` — `StateEventBus` over `tokio::sync::broadcast`, capacity 256; events: `StateEvent::Updated / OpApplied / Pending / TimelineJump`.
- `rust/crates/rpg-state/src/migrate.rs` — v1→v6 upgrade chain (run unconditionally on `from_value`).
- `rust/crates/rpg-state/src/structured.rs` (883 lines) — extracts 【…】 tags + ```json``` ops from LLM output.
- `rust/crates/rpg-state/src/directives.rs` — `/set` / `/reveal` player directive handling.
- `rust/crates/rpg-state/src/pending.rs` — `approve_pending_write` / `reject_pending_write`.
- `rust/crates/rpg-state/src/rules_gameplay.rs` — `add_memory_item` / `update_relationship` / `record_hypothesis`.
- `rust/crates/rpg-state/src/timeline_jump.rs` (328 lines) — three-phase time-jump protocol.
- `rust/crates/rpg-state/src/worldline_validation.rs` — `_scan_worldline_validation` / `_set_worldline_validation` / `_store_worldline_projection`.
- `rust/crates/rpg-state/src/combat_state.rs` — RulesEngine ingress: `update_active_entities` / `append_dice_log` / `update_encounter` / `upsert_active_entity` / `prune_active_entities` / `clear_encounter`.
- `rust/crates/rpg-state/src/script_overrides.rs` — `load_script_overrides` (save_id → script_id → DB via `rpg-db::repos::script_overrides`).
- `rust/crates/rpg-state/src/rules_outcome.rs` — outcome shaping for rules dispatcher.

Rules (`rpg-rules` + `rpg-rules-bridge`):
- `rust/crates/rpg-rules/src/lib.rs` — re-exports.
- `rust/crates/rpg-rules/src/dice.rs` (125 lines) — RNG, expression parser.
- `rust/crates/rpg-rules/src/engine.rs` (310 lines) — `RulesEngine` facade.
- `rust/crates/rpg-rules/src/modules.rs` (252 lines) — JSON module loader; resolves `RPG_MODULES_DIR` → `./rpg/modules`. Only shipped module: `rpg/modules/ash_mine/`. Empty dir on disk currently — the JSON loader returns `Vec::new()` rather than error.
- `rust/crates/rpg-rules/src/dnd5e/` — `actions.rs / character.rs / checks.rs / combat.rs / monsters.rs / ruleset.rs` (D&D 5E SRD facets).
- `rust/crates/rpg-rules-bridge/src/checks.rs / combat.rs / consume.rs / intent.rs / suggest.rs` — bridge between LLM intent suggestions and rules engine outcomes.

Scenes, encounters, inventory: live inside the typed `GameStateData` schema:
- `rust/crates/rpg-schemas/src/game_state.rs` — `GameStateData` (~17 top-level fields).
- `rust/crates/rpg-schemas/src/rules.rs` — `Ruleset` + module ids (defaults to `"ash_mine"`).
- `rust/crates/rpg-schemas/src/game.rs / core.rs / common.rs` — scenes, encounter, character.

Retrieval (`rpg-retrieval`):
- `rust/crates/rpg-retrieval/src/lib.rs` (902 lines, single file) — BM25-lite ranker, jieba-style CJK tokenization, deterministic top-k. 21 tests inline.

Agents (`rpg-agents`, ~7k LoC):
- `rust/crates/rpg-agents/src/lib.rs` — module map.
- `rust/crates/rpg-agents/src/gm.rs` (642 lines) — `GameMaster::step()` is the canonical chat loop entry; pulls context, dispatches tools, returns chunk stream.
- `rust/crates/rpg-agents/src/common.rs` (640 lines) — shared chunk plumbing.
- `rust/crates/rpg-agents/src/command_agent.rs` — `/`-style command parsing.
- `rust/crates/rpg-agents/src/context_agent.rs` (398 lines) — slot-by-slot context layer collection.
- `rust/crates/rpg-agents/src/extractor.rs` — structured-update extraction.
- `rust/crates/rpg-agents/src/anchor_seed_agent.rs` (759 lines).
- `rust/crates/rpg-agents/src/black_swan_agent.rs` (994 lines) + `validator_independent_critic` (Wave 7-D).
- `rust/crates/rpg-agents/src/phase_digest_agent.rs` (602 lines).
- `rust/crates/rpg-agents/src/worldbook_agent.rs` (924 lines).
- `rust/crates/rpg-agents/src/timeline_narrative_guard.rs` (275 lines).
- `rust/crates/rpg-agents/src/acceptance_verifier.rs` (153 lines).
- `rust/crates/rpg-agents/src/prompts/` — sub-agent system prompts as Rust string consts.

### Test coverage (evidence)
- `rust/crates/rpg-state/` — 58 `#[test]` markers across `typed_path.rs (15)`, `ops.rs (12)`, `migrate.rs (12)`, `rules_outcome.rs (9)`, `directives.rs (5)`, `store.rs (4)`, others.
- `rust/crates/rpg-rules/` — 5 tests across `engine.rs / modules.rs / dice.rs`.
- `rust/crates/rpg-rules-bridge/` — 8 tests (`suggest.rs:8`, `combat.rs`, `consume.rs`, etc.).
- `rust/crates/rpg-retrieval/src/lib.rs` — 21 inline tests.
- `rust/crates/rpg-agents/` — 64 tests; heaviest in `black_swan_agent.rs (15)`, `worldbook_agent.rs (12)`, `anchor_seed_agent.rs (11)`, `gm.rs (7)`, `context_agent.rs (7)`.
- `rust/crates/rpg-context/` — 37 tests; `chars_cache.rs (8)`, `engine.rs (6)`.

### Stubs / TODOs / panics in this row
- `rust/crates/rpg-context/src/provider.rs:34` — `TODO[接入]` injection of `rpg_llm::vertex::VertexBackend::embed` (used by retrieval providers).
- `rust/crates/rpg-context/src/provider.rs:55` — example doc-comment with same TODO.
- `rust/crates/rpg-context/src/providers/novel.rs:186` — `// TODO: 等 rpg-state 提供 state.set_last_retrieval(text)`.
- `rust/crates/rpg-context/src/providers/novel.rs:193` — `// TODO[接入]` for `db_pool + embed_fn` simultaneous path.
- No `todo!()` or `unimplemented!()` in this row.
- Module headers in `rpg-platform/src/branches/mod.rs` still say "骨架/skeleton" for some submodules — **stale**; bodies are complete after Wave 14.

### Invariants the migration AI must preserve
- All state mutations go through `apply_op` (`rpg-state/src/ops.rs`) so the 5-gate filter + audit_log + pending_writes + event bus all fire. **Never** mutate `GameState.data` fields directly.
- `GameState.data` is typed (`rpg_schemas::GameStateData`); to add a field, add it to the schema crate, regen ts-rs, and add a `typed_path` dispatch arm.
- `CURRENT_SCHEMA_VERSION = 6` (`rpg-state/src/state.rs`). To bump, add a `vN→vN+1` step in `migrate.rs` **and** the unconditional `from_value` upgrade still runs once.
- `StateStore` key is `String`, not `UserId`. Anonymous user uses sentinel `"anonymous"`. Don't tighten this without first handling anonymous flow.
- `StateEventBus` channel capacity is 256; lagged subscribers silently drop. Slow consumers must reconnect.
- `get_path` now returns `Option<Value>` (owned), not `Option<&Value>` — typed fields have no persistent `Value`.
- Hard-forbidden paths (`_HARD_FORBIDDEN_PATHS / _PREFIXES`) and rules-managed paths are PHF sets at compile time. Tweaking them affects all sources.
- Module-managed paths only reject when `scene.module_id` is non-empty and source is GM.
- Anything written by users must also call `mark_user_locked` — handled inside `apply_op`.

### (No next steps — row is ✅.)

---

## Row 2: LLM routing (✅)

### What's implemented (evidence)
- `rust/crates/rpg-llm/src/lib.rs:1-40` — crate overview.
- `rust/crates/rpg-llm/src/pipeline.rs` (684 lines) — `ChatRole / ChatMessage / MessagePart / ChatRequest / ChatChunk / LlmBackend trait / Usage / BackendKind / ToolCallAccumulator / build_http_client / namespaced_tool_name / extra_headers`. **Stop reading other backends until you've read this.** It defines the contract every backend implements.
- `rust/crates/rpg-llm/src/anthropic.rs` (1100 lines, 6 tests) — Messages API + SSE; covers `message_start / content_block_start / content_block_delta / content_block_stop / message_delta / message_stop`; tool_use accumulator at `pipeline.rs:389-407`. Extended-thinking via `thinking_delta` / `signature_delta` → `ChatChunk::Thinking`; thinking config via `req.extra.thinking` / `req.extra.thinking_budget`.
- `rust/crates/rpg-llm/src/anthropic.rs:69` — TODO[auth] note about premium-model gating (non-blocking).
- `rust/crates/rpg-llm/src/openai.rs` (558 lines) — Chat Completions + streaming, base_url override, tool_calls partial-args accumulation.
- `rust/crates/rpg-llm/src/responses.rs` (621 lines) — OpenAI `/v1/responses` endpoint (separate from Chat Completions), maps `response.reasoning_summary.delta` → `ChatChunk::Thinking`.
- `rust/crates/rpg-llm/src/vertex.rs` (844 lines, 16 tests) — Gemini via `yup-oauth2` ServiceAccount; SSE via `?alt=sse`; `embed_with_task_type` for retrieval embeddings; multimodal `inlineData / fileData`.
- `rust/crates/rpg-llm/src/any_backend.rs:7-145` — `AnyBackend` sum enum (Anthropic / Vertex / OpenAi / Responses) that itself `impl LlmBackend`, so call-sites stay polymorphic without dyn.
- `rust/crates/rpg-llm/src/registry.rs` (832 lines) — `ModelCatalog` schema_version=1 (Python-compatible) + `LlmRouter::pricing_for` + `BUILTIN_PRICING` lazy map.
- `rust/crates/rpg-llm/src/simd_parse.rs` (201 lines, 16 tests) — Wave 10-C SSE hot-path JSON via `simd-json` with `serde_json` fallback.
- `rust/crates/rpg-llm/src/metrics.rs` — `record_llm_request` / `record_llm_tokens` Prometheus counters.

Frontend wire types are exported from this crate (see Row 4).

### Test coverage (evidence)
- 47 inline `#[test]` markers in `rpg-llm/src/`. Heaviest: `vertex.rs (16)`, `simd_parse.rs (16)`, `pipeline.rs (7)`, `anthropic.rs (6)`.
- Bench: `rust/crates/rpg-llm/benches/` exists (Wave 10-D criterion).
- Wire-protocol tests live in `model_catalog/tests/` (see Row 7).

### Stubs / TODOs / panics in this row
- `rpg-llm/src/anthropic.rs:69` — `TODO[auth]: if req.model.contains("opus") && !authorized_for_premium {…}` — non-blocking, just a permissions hook.
- `rpg-llm/src/openai.rs` module header notes `reasoning_tokens / response_format` left as edge TODOs (no inline marker).
- No `todo!()` / `unimplemented!()`.

### Invariants the migration AI must preserve
- `ChatChunk` is the **only** stream type. Backends MUST merge tool-use partial-JSON internally and emit `ChatChunk::ToolCall` exactly once per call (signal at `content_block_stop`).
- `ToolCallAccumulator::finalize` is fallible; use `finalize_lossy` only when explicitly tolerating partial JSON.
- `namespaced_tool_name(server_id, tool)` and `split_namespaced(full)` are the canonical MCP namespacing — don't roll your own concat.
- `build_http_client(timeout_secs)` centralizes reqwest config (HTTP/2, TLS, etc.). Use it.
- `BUILTIN_PRICING` is a Lazy `HashMap` keyed by `"{api_id}::{model_id}"`. Catalog inline pricing wins; this is the fallback.
- ModelCatalog `schema_version = 1` is shared with the legacy Python `model_catalog.json`. Bumping requires a migration path.
- Extended thinking config goes through `ChatRequest.extra.thinking` / `thinking_budget`; `merge_thinking_extra` + `build_thinking_extra` handle the wire shape.

---

## Row 3: Postgres + pgvector, 24 migrations (✅)

### What's implemented (evidence)
- `rust/crates/rpg-db/src/lib.rs` — re-exports `PgPool`, `migrations`, `pool`, `repos`, `metrics`.
- `rust/crates/rpg-db/src/pool.rs` — `init_pool / init_pool_with_opts`. Defaults: `min_connections=2`, `idle_timeout=600s`, `max_lifetime=1800s`, `acquire_timeout=5s`, per-conn `SET statement_timeout = 5000ms`. **Pgbouncer transaction-mode safe**.
- `rust/crates/rpg-db/src/migrations.rs` — `MigrationStep { id, name, sql }`; `MIGRATIONS: &[…]` at lines 80-112; SQL `include_str!` from `rust/crates/rpg-db/migrations/001..024_*.sql`. `run_migrations(pool)` at line 155 uses `pg_try_advisory_lock` (polling, not blocking — won't pin a sqlx connection) with `LOCK_TIMEOUT_MS`; ensures `schema_migrations` table; applies in order.
- `rust/crates/rpg-db/migrations/` — all 24 files present: `001_init.sql` (full Python parity schema), `002..016` (one-to-one with Python `MIGRATIONS` list), `017_sessions_hashed_token.sql`, `018_stop_signals.sql` (cross-pod stop signaling), `019_runtime_checkouts.sql`, `020_user_card_public_audit.sql`, `021_scripts_and_chapters.sql`, `022_branches_extended_columns.sql`, `023_phase_digests.sql`, `024_provider_rename.sql` (Wave 11.5-A).
- `rust/crates/rpg-db/src/metrics.rs` — `query_timed!` macro + histogram/counter (Wave 9-C).
- `rust/crates/rpg-db/src/repos/` — typed row helpers: `character_cards / import_jobs / phase_digests / save_phase_digests / save_worldbook_overlays / script_overrides / token_usage / user_credentials / worldbook_entries / mod.rs`.
- Boot-time wiring: `rust/crates/rpg-server/src/main.rs` runs migrations after pool init; `RPG_SKIP_AUTO_MIGRATE=1` skips.

### Test coverage (evidence)
- 12 `#[test]` markers in `rpg-db/src/` (mainly `migrations.rs:9` — static sanity checks on the `MIGRATIONS` slice: monotonic ids, non-empty names, non-empty SQL).
- DB-touching integration tests live in `rust/crates/rpg-server/tests/e2e.rs` (gated by `--features e2e` + `RPG_TEST_DB_URL`, see Section C).

### Stubs / TODOs / panics in this row
- None. No `todo!()`/`FIXME`.
- `rpg-platform/src/cluster.rs:10` note: "stop_signals DDL handled by rpg-db migrations" — confirms the DDL contract; no stub.

### Invariants the migration AI must preserve
- **Migrations are append-only.** Never edit a numbered `.sql` file. Add `025_*.sql`, register a new `SQL_025` `include_str!`, append `MigrationStep { id: 25, … }` at end of `MIGRATIONS`.
- `pg_try_advisory_lock` uses a single fixed key (see `migrations.rs`). Do not change the key — multi-pod start-up correctness depends on it.
- v001 is the canonical full schema; later migrations are idempotent (`create if not exists`) so they no-op on a fresh DB but stay compatible with the Python sibling.
- `pgbouncer` is transaction-pooling — prepared statements with persistent names are unsafe. sqlx is already configured to use anonymous prepares; **do not** add `PREPARE`/`SET SESSION`/`LISTEN`/`NOTIFY` SQL.
- `statement_timeout=5000ms` is per-connection. Long pgvector scans need `set_statement_timeout` per transaction.
- Connection limits: `acquire_timeout=5s` is intentionally tight to avoid request pile-up. Don't raise.

---

## Row 4: ts-rs typed frontend (✅)

### What's implemented (evidence)
- 43 generated `.ts` files in `frontend/src/types/rust/` (verified with `find . -name '*.ts' | wc -l`):
  - Top-level (15): `AuditEntry, Encounter, GameStateData, Memory, PendingWrite, PermissionsState, PlayerCharacter, PlayerInfo, PlayerPrivate, Ruleset, Scene, TimelineState, World, Worldline, WorldlineValidation`.
  - `catalog/` (5): `ProviderId, ModelCapabilities, CatalogSource, ModelInfo, index.ts`.
  - `events/` (23): `WsHelloPayload, WsClientMessage, Op, ToolSchema, StateEvent, SseStateChangePayload, SseStateBusPayload, SseHelloPayload, SseChunkPayload, SseDonePayload, SseEnvelope, SseErrorPayload, WsServerMessage, WsErrorPayload, ChatRequest, ChatMessage, ChatRole, WireChatChunk, ModelInfo, BackendKind, ToolCall, MessagePart, Usage`.
- Generation contract — `#[cfg_attr(feature = "ts-rs", derive(TS))] #[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/…"))]` annotations:
  - `rust/crates/rpg-schemas/src/game_state.rs` — 10 derive sites; exports top-level types.
  - `rust/crates/rpg-state/src/{bus,ops,...}.rs` — feature `ts-rs` exports `events/StateEvent.ts`, `events/Op.ts`, etc.
  - `rust/crates/rpg-llm/src/pipeline.rs` — exports `ChatChunk → WireChatChunk`, `ChatMessage`, `BackendKind`, etc. to `events/`.
  - `rust/crates/model_catalog/src/schema.rs:25-118` — exports `ModelInfo, ModelCapabilities, ProviderId, CatalogSource` to `catalog/`.
- Cargo features triggering generation:
  - `rust/crates/rpg-llm/Cargo.toml:34` — comment: "cargo test -p rpg-llm --features ts-rs".
  - `rust/crates/rpg-state/Cargo.toml:29` — same idiom.
- Frontend wiring:
  - `frontend/Login.html / Platform.html / Game Console.html` — 3 Vite HTML entries.
  - `frontend/src/entries/login.jsx, platform.jsx, game-console.jsx` — JSX entry points.
  - `frontend/src/api-client.js` — hand-rolled REST/SSE client (no codegen).
  - `frontend/vite.config.js` — proxies `/api → http://localhost:7860`.
  - `frontend/TYPESCRIPT.md` — devs' note on type generation.

### Test coverage (evidence)
- ts-rs is exercised indirectly by the export tests. Each `rpg-schemas / rpg-state / rpg-llm / model_catalog` test run with `--features ts-rs` emits + diff-checks files.
- No standalone JS unit tests in `frontend/` (just integration harnesses `frontend/test-spark-and-merge.js`, `frontend/test-integration.sh`).

### Stubs / TODOs / panics in this row
- None tracked in code. Generation is "run tests with the feature flag" — there is no scheduled CI step ensuring the on-disk `.ts` files match Rust types (see Section D).

### Invariants the migration AI must preserve
- Touching any `#[cfg_attr(feature = "ts-rs", derive(TS))]` struct requires regenerating bindings: run `cargo test -p <crate> --features ts-rs` and commit the diff under `frontend/src/types/rust/`.
- `export_to` paths in source use `../../../../frontend/src/types/rust/…` relative to the source file. Do not change the path string without coordinating both ends.
- Top-level types go to `frontend/src/types/rust/`, catalog types to `catalog/`, event/wire types to `events/`. Don't mix.
- Frontend imports use these typed files; renaming a Rust field is a breaking change for the client.

---

## Row 5: Branchable saves (🟡)

### What's implemented (evidence)
Living in `rust/crates/rpg-platform/src/branches/` (3638 lines, 11 files):

- `mod.rs` (44 lines) — `BranchService { pool }` facade + re-exports. **Header comment claims `seed/runtime/maintenance/deletion` are still skeleton/TODO — STALE; bodies are real after Wave 14.**
- `commits.rs` (318 lines, 4 tests inline):
  - `BranchCommit` struct at line 17; `object_hash` (71), `state_file_hash` (79), `state_snapshot_hash` (91), `commit_for_user` (154), `insert_commit` (183), `insert_commit_with_tx` (228). Git-like hashing of state snapshots.
- `refs.rs` (440 lines, 5 tests):
  - `BranchRef` (21), `upsert_ref` (50), `upsert_ref_with_tx` (76), `upsert_ref_by_id` (119), `find_or_create_ref_for_commit` (161), `ensure_active_ref` (192), `set_save_active` (271), `set_save_active_with_tx` (304), `write_checkout` (355). The `is_active`/`active` field-name confusion is documented at line 10.
- `tree_ops.rs` (331 lines):
  - `TreePage` (18), `TreeResult` (26), `tree` (66), `collect_ids` (223), `resolve_commit_id_by_message` (248), `round_start_node` (306). **All implemented**, despite header comment at line 4 calling the last two TODO.
- `helpers.rs` (277 lines) — text utils, state file I/O, `load_state`, `commit_state`, `snapshot_for_history`, `rough_summary`, `write_snapshot`, `unlink_branch_state`, `MAIN_REF`. Regex statics at lines 15-21 (fixed CJK normalizers).
- `activation.rs` (212 lines) — `continue_from` (21), `activate_node` (66), `activate_save` (110). All call `set_save_active + write_checkout + runtime::activate_state_snapshot`.
- `seed.rs` (283 lines) — `seed_tree` 3-branch logic (existing commit / legacy nodes migration / fresh). Header line 6 calls `_seed_and_bootstrap` TODO — the function is **not** present in Rust because Rust uses `activate_state_snapshot` directly.
- `runtime.rs` (672 lines, 7 tests inline) — `RecordedTurn` (29), `record_runtime_turn` (47), `persist_runtime_state` (234), `bootstrap_runtime_binding` (332), `mark_runtime_dirty` (457). Wave 14 wired this end-to-end (commit `1689857b`).
- `summary.rs` (476 lines, 10 tests) — `LLM_SUMMARY_SYSTEM` const (31), `init_summary_backend` (59), `generate_summary_now` (77), async `schedule_llm_summary`. Wired to `AnyBackend` via `OnceCell` injected at startup. Falls back to `rough_summary` placeholder when not injected — non-blocking.
- `maintenance.rs` (198 lines) — `ensure_summaries`, `ensure_state_snapshots`. Boot-time backfill.
- `deletion.rs` (387 lines) — `delete_subtree` (37) full path: trash refs + branch_commits delete + active fallback + file unlinks + runtime re-activation. `rollback_to_message` (167) — full multi-table cleanup (`messages / save_timeline_anchors / context_runs / save_phase_digests`) + trash ref + active switch + runtime activation.

Routes that drive this row: `rust/crates/rpg-routes/src/branches.rs` (284 lines), `rust/crates/rpg-routes/src/saves.rs` (664 lines).

### Test coverage (evidence)
- `branches/commits.rs` — 2 tests (object_hash determinism, connect-lazy signature check).
- `branches/refs.rs` — 1 test (`BranchRef` JSON roundtrip).
- `branches/runtime.rs` — 7 signature-stability/connect-lazy tests (lines 590, 600, 610, 622, 631, 641 connect to non-existent DB).
- `branches/summary.rs` — 10 tests including LLM prompt assembly.
- `branches/maintenance.rs` — 1 `signatures_stable` compile-only test.
- `branches/deletion.rs` — 1 test `rollback_negative_index_rejects` (validation-only path, no DB).
- **No real DB integration tests** for the branch graph — the e2e harness in `rpg-server/tests/e2e.rs` exercises higher-level routes but does not cover delete-subtree/merge.

### Stubs / TODOs / panics in this row
- `rust/crates/rpg-platform/src/branches/mod.rs:11` — `// seed: 骨架(细节 TODO)` — **stale, body complete**.
- `rust/crates/rpg-platform/src/branches/mod.rs:13` — `// summary: TODO(依赖 rpg-llm pipeline)` — **stale, OnceCell injection landed**.
- `rust/crates/rpg-platform/src/branches/mod.rs:14` — `// maintenance/deletion: TODO 占位` — **stale, both shipped**.
- `rust/crates/rpg-platform/src/branches/tree_ops.rs:4` — `resolve_commit_id_by_message / round_start_node TODO` — **stale**.
- `rust/crates/rpg-platform/src/branches/seed.rs:6` — `_seed_and_bootstrap 仍 TODO` — Python-only entry that Rust replaced with `activate_state_snapshot`.
- `rust/crates/rpg-platform/src/branches/summary.rs:16` — comment "(仅写 placeholder, P2-LLM TODO 不阻断)" — describes fallback path when no backend injected.
- `rust/crates/rpg-platform/src/branches/helpers.rs:261` — `// TODO[Sonnet]: display_nodes(rows)` — frontend UI consolidation of player+gm into round nodes; backend exposes raw rows.
- **Actually missing for ✅**: **merge** of two branches; **cleanup** (GC of orphaned commits/refs); **deletion-by-policy** (e.g. trash older than N days). README accurately calls these out.
- No `todo!()` / `unimplemented!()` panics anywhere in `branches/`.

### Invariants the migration AI must preserve
- Branch-commit hashing is order-invariant (`object_hash` sorts keys). State snapshots hashed via `state_snapshot_hash`. Don't change either or commit IDs become unstable across versions.
- All write-path branch ops route through `commits.rs:insert_commit_with_tx` + `refs.rs:upsert_ref_with_tx` in the same `pool.begin()` transaction. New flows must follow.
- `delete_subtree` performs file unlinks **outside** the transaction (intentional — orphan file is recoverable, partial-state DB is not).
- `rollback_to_message` writes a `refs/trash/{ts}-msg{N}` ref before destruction, so deletions are recoverable via the trash ref.
- `MAIN_REF = "refs/heads/main"` is the canonical primary ref. Never delete it.
- `set_save_active` / `write_checkout` must be called together when changing the active commit — the former updates `game_saves`, the latter writes the checkout audit row.
- The `is_active` column on `branch_refs` was historically miswritten as `active`; current schema uses `is_active`. Don't accidentally revert.
- `branch_commits.state_snapshot` is `jsonb`; `state_path` points to a flat file backup. `commit_state(snapshot, path)` resolves the canonical state, preferring `snapshot` when non-null/non-`{}`.

### Concrete next steps to take this 🟡 → ✅
1. **Implement merge.** Reference Python `branches/merge.py` (if present in `rpg/platform_app/branches/`). Implement `merge(pool, user_id, save_id, source_ref, target_ref)` in a new `branches/merge.rs`. Re-use `state_snapshot_hash` for conflict detection. Add tx-bounded multi-ref reassignment. Cover with a fixture-based test (mock-pool not enough; add to `e2e.rs`).
2. **Implement cleanup/GC.** Walk `branch_commits` not reachable from any non-trash ref and not within last N days; remove with the same tx pattern as `delete_subtree`. Add an admin route (`/api/admin/branches/gc`).
3. **Add policy-driven deletion** (trash > 30d, abandoned branches). Schedule via `tokio::time::interval` in `rpg-server/src/main.rs` lifespan startup.
4. **Add real DB integration tests** for `delete_subtree` / `rollback_to_message` under `--features e2e` in `rpg-server/tests/e2e.rs`. Current unit tests only hit validation paths.
5. **Front-end consolidation** of player+gm rows into round nodes (`helpers.rs:261` TODO) is a route/jsx job, not a branches-crate job; leave the helper, fix the client.

---

## Row 6: Script pack (🟡)

### What's implemented (evidence)

Import side (works):
- `rust/crates/rpg-platform/src/script_import/mod.rs` (662 lines) — `ImportSource` enum (`Bytes` / `Upload`), `ImportResult`, `import_script` end-to-end (bytes → split chapters → write `scripts` + `script_chapters` → spawn embedding job → return `script_id`), `schedule_knowledge_sync` (dedup via `import_jobs` table), `get_sync_status`.
- `rust/crates/rpg-platform/src/script_import/upload.rs` (493 lines, 12 tests) — `init_upload`, `put_chunk`, `finish_upload`, `cancel_upload`, `consume`. Disk path `<UPLOAD_ROOT>/user_<id>/<upload_id>/`, idempotent re-upload of same chunk_index. `max_upload_chunk_bytes()` reads `RPG_UPLOAD_CHUNK_MAX_BYTES` (default 8 MiB); `max_script_upload_bytes()` reads `RPG_SCRIPT_UPLOAD_MAX_BYTES` (default 256 MiB).
- `rust/crates/rpg-platform/src/script_import/splitter.rs` (1675 lines, 29 tests) — `decode_bytes` (utf-8/utf-8-sig/gb18030/gbk/big5), `clean_text` (BOM, whitespace, piracy watermarks), `split_chapters_with_report` with 8 modes: `auto / chapter_cn / chapter_en / corpus / number_dot / paren_num / custom / remulina_special / pagination_headings / numbered_sections` (Wave 8-C added the last three).

Pack (share-format) side:
- `rust/crates/rpg-platform/src/script_pack.rs` — `FORMAT_VERSION = 1`, `MAX_ZIP_BYTES = 50 MiB`. `ScriptPackRow` struct + `init_script_packs_table` (DDL not in numbered migrations — created on demand), `create_pack`, `list_packs`, `get_pack`, `list_packs_by_script`, `delete_pack`, `parse_manifest`, `compute_checksum`.

Routes (`rust/crates/rpg-routes/src/scripts.rs`, 2560 lines):
- `POST /api/scripts/import` (line 364) → `import_script(Bytes | Upload)`.
- `POST /api/scripts/batch-import` (line 460) → multi-file import loop.
- `POST /api/scripts/import-pack` (line 575) → `extract_pack_from_zip` + per-table insert. **This handler is the import side of the share surface.**
- `GET  /api/scripts/{id}/export-pack` (line 143) → `build_export_zip` (manifest + chapters + characters + worldbook). **This handler is the export side.**
- `POST /api/scripts/{id}/resplit`, `POST /api/scripts/{id}/knowledge/sync`, `POST /api/scripts/{id}/embed`, `GET .../import-status` etc.

Knowledge-sync hooks:
- `rust/crates/rpg-platform/src/knowledge/embedding.rs` — `spawn_embed_script` (tokio::spawn, fire-and-forget, in-process). `import_jobs` row tracks progress.

### Test coverage (evidence)
- `script_import/splitter.rs` — 29 tests (each split mode + edge cases).
- `script_import/upload.rs` — 12 tests (chunk reassembly, ID validation, idempotence).
- `script_import/mod.rs` — 5 tests including line 643 `panic!("expected Bytes")` inside a test (not production).
- `knowledge/embedding.rs` — 10 tests, line 544 `panic!("unexpected error variant")` is test-only.
- `script_pack.rs` — no inline tests visible.
- Pack export `build_export_zip` (scripts.rs:224) — no test.

### Stubs / TODOs / panics in this row
- `rust/crates/rpg-platform/src/script_import/mod.rs:16` — `TODO[P2-SYNC]` heartbeat / recover_pending_sync_jobs not implemented (single-process tokio supervision is enough).
- `rust/crates/rpg-platform/src/script_import/mod.rs:18` — `TODO[P2-LLM]` script summary not generated at embed time.
- `rust/crates/rpg-platform/src/script_import/mod.rs:19` — same as :16.
- `rust/crates/rpg-platform/src/script_import/mod.rs:501` — `// (TODO[P2-SYNC]: 让 spawn_embed_script 完成时回写 import_jobs.status='done'.)` — currently no terminal-status write-back.
- `rust/crates/rpg-platform/src/library.rs:638` — `// tar/gz 留 TODO;目前只展开 zip.` — limits pack format.
- `rust/crates/rpg-server/src/main.rs:476` — `// TODO[rpg-platform]: 真正 requeue/transition 等 recover_pending_sync_jobs 落地.`
- `rust/crates/rpg-platform/src/knowledge/mod.rs:18` — `TODO[Sonnet]: script_pack / script_overrides / session / worldline / context_runs` — knowledge re-exports not yet covering pack helpers.
- `rust/crates/rpg-platform/src/script_pack.rs` creates its DDL on-demand (`init_script_packs_table`) **outside** of numbered migrations — this is a divergence from the migrations discipline (see Row 3) and should be normalized.

### Invariants the migration AI must preserve
- `import_jobs` is the durable progress journal — every script import / embedding job writes a row. v013 (`013_import_jobs_single_active_per_script.sql`) enforces uniqueness per script — don't bypass.
- `decode_bytes` order matters (utf-8 → utf-8-sig → gb18030 → gbk → big5). Don't reorder; corpus files depend on this fallback chain.
- Chunk filenames `chunk_XXXX.bin` are zero-padded 4 digits. `meta.json` keys are stable; don't rename `received_chunks` / `received_bytes`.
- `upload_id` format `up_<user_id>_<16hex>` is prefix-checked for tenant isolation. Don't change format.
- ZIP body limit 50 MiB (`MAX_ZIP_BYTES`); soft upload cap from env.
- Pack manifest format_version=1; bumping requires backward-compatible reader.
- `parse_manifest` returns `serde_json::Value` — preserve unknown fields for forward-compat.

### Concrete next steps to take this 🟡 → ✅
1. **Move script_pack DDL into a numbered migration** (`025_script_packs_table.sql`) and delete `init_script_packs_table`. Audit other on-demand DDLs.
2. **Build the public sharing surface**: routes `POST /api/packs/{id}/publish`, `GET /api/packs/public`, copy-on-import semantics. Right now packs are owner-scoped only.
3. **Wire embedding job completion back to `import_jobs.status='done'`** (`script_import/mod.rs:501`). Without this, the UI can't reliably show "sync done".
4. **Add heartbeat + `recover_pending_sync_jobs`** for cross-restart durability (currently lost on process crash).
5. **Add pack import idempotence**: same checksum + same owner = no-op. Add an integration test that imports the same pack twice.
6. **Support tar.gz** in `library.rs:638` — or document it as an explicit non-goal.

---

## Row 7: Provider catalog (🟡)

### What's implemented (evidence)
- `rust/crates/model_catalog/Cargo.toml` — independent crate, depends only on `serde + reqwest + chrono + thiserror + dashmap + ts-rs`.
- `rust/crates/model_catalog/src/schema.rs` (141 lines) — `ModelInfo` struct (id, provider, capabilities, costs, deprecation dates, source, last_updated), `ModelCapabilities` (11 booleans), `ProviderId` enum (10 variants), `CatalogSource` enum (`LiveApi / StaticCatalog / UserOverride / OpenRouterProxy`), `CatalogError`. All four exported via ts-rs.
- `rust/crates/model_catalog/src/catalog.rs` (290 lines) — `Catalog { cache, http, overrides, api_keys }` with TTL-based per-provider cache. Constants: `KNOWN_OPENAI_COMPAT_PROVIDERS` (6: OpenAI, OpenRouter, DeepSeek, XAi, XiaomiMimo, TencentHunyuan), `KNOWN_NATIVE_PROVIDERS` (4: Anthropic, GoogleAIStudio, AgentPlatform, AlibabaQwen), `KNOWN_ALL_PROVIDERS` (10). `refresh` (line 133) → `refresh_openai_compat` (148) / `refresh_native` (188). Fallback chain: live → static; never empty (Vec on failure).
- `rust/crates/model_catalog/src/providers/` (12 files):
  - **6 wired with real backend** (OpenAI-compat protocol — runtime calls in `rpg-llm/src/openai.rs`): `openai.rs (21 lines)`, `openrouter.rs (35)`, `deepseek.rs (23)`, `xai.rs (21)`, `xiaomi_mimo.rs (32)`, `tencent_hunyuan.rs (27)`.
  - **4 native backends wired**: `anthropic.rs (204 lines)` — real `/v1/models` fetch; `google_ai_studio.rs (157)` — real `/v1beta/models` fetch; `agent_platform.rs (259)` — Vertex via SA JSON; `alibaba_dashscope.rs (65)` — **static-only** (`fetch_models` returns `static_catalog()` because DashScope has no `/models` endpoint).
  - **Catalog-only / runtime-stubbed**: README claims "4 catalog-only" but the **schema** keeps all 10 as `ProviderId` variants. Confirmed mismatch: code lists **10 providers, 10 catalogs, 9 live `/models` endpoints, 1 static-only (Alibaba)**. The README's "catalog-only" framing refers to *which providers have actual `rpg-llm` Backend impls available for chat at runtime* — **only 4 backends exist in `rpg-llm`** (`AnthropicBackend / VertexBackend / OpenAiBackend / ResponsesBackend`), so the 6 OpenAI-compat providers all share `OpenAiBackend` with a `base_url` override, while AlibabaQwen / GoogleAIStudio runtime chat is not yet wired (catalog only). Agent Platform = Vertex (wired). Verify before assuming.
- Provider routing chain (`rpg-llm/src/registry.rs:LlmRouter`): `selected.api_id → BackendKind` → call `register(api_id, Arc<AnyBackend>)`. Backends supported (`any_backend.rs:120-138`): `Anthropic, Vertex, OpenAi, Responses`.
- Pricing data is per-`ModelEntry` + `BUILTIN_PRICING` fallback (`rpg-llm/src/registry.rs:133`).
- Static JSON catalogs in `rust/crates/model_catalog/data/`: 10 files, one per provider.

### Test coverage (evidence)
- `rust/crates/model_catalog/tests/`:
  - `static_catalogs.rs` — 6 tests (every static JSON parses + has the expected models).
  - `native_providers.rs` — 13 tests (anthropic / vertex / google / alibaba parse paths, error handling).
  - `openai_compat_fetch.rs` — fetch happy path + 401 / 5xx / empty.
  - `catalog_aggregator.rs` — `KNOWN_ALL_PROVIDERS` aggregation + cache TTL.
  - `schema_roundtrip.rs` — ts-rs JSON parity.
- 4 inline tests in `src/`.

### Stubs / TODOs / panics in this row
- `rust/crates/model_catalog/src/providers/alibaba_dashscope.rs:46` — `fetch_models` is intentionally a static-catalog wrapper (no live endpoint). Not a stub per se but a documented limitation.
- `rust/crates/rpg-platform/src/usage.rs:400` — `TODO[P2-LLM]` ModelEntry context_tokens.
- `rust/crates/rpg-platform/src/usage.rs:402` — re-uses `PRICING_ROUTER.pricing_for` for validity check; not stub but inelegant.
- No `todo!()` / `unimplemented!()`.

### Invariants the migration AI must preserve
- **`ProviderId` enum order = wire ABI**. ts-rs exports the variant names; renaming/reordering breaks the frontend. Append-only.
- Each provider has a `slug()` returning the stable lowercase id used as cache key + UI slug.
- Provider api-key resolution order: `set_api_key` > env var > None. `AgentPlatform` always None (uses SA JSON via separate env).
- `KNOWN_*` constant slices are the single source of truth for batch refresh / preload. Don't hardcode lists elsewhere.
- Catalog TTL: `Catalog::new(ttl)` — typically several minutes. `refresh()` insertion replaces the timestamp atomically.
- `CatalogSource::OpenRouterProxy` is reserved for pricing aggregated through OpenRouter — currently unused in code but reserved for future use.
- Pricing units are USD per **million** tokens (`input_cost_per_million`), not per 1k. `ModelPricing` in `rpg-llm` uses **per 1k**. Don't conflate.

### Concrete next steps to take this 🟡 → ✅
1. **Wire `GoogleAIStudio` as a runtime backend** in `rpg-llm/` — currently the Vertex backend (`VertexBackend`) only handles SA-auth `aiplatform.googleapis.com`. Add a thin `GoogleAiStudioBackend` (or extend Vertex with `x-goog-api-key`).
2. **Wire `AlibabaQwen` as a runtime backend** — DashScope native protocol (see `alibaba_dashscope.rs` header for endpoint spec); shares no wire shape with OpenAI compat so a new backend is needed.
3. **Capability matrix in code, not docs**: add `ModelCapabilities` integration tests that verify every model marked `vision: true` actually accepts multimodal requests through its routed backend.
4. **Catalog refresh scheduler**: add a periodic `tokio::time::interval` in `rpg-server` lifespan startup that calls `Catalog::refresh` for each provider with a valid API key.
5. **Hot-reload UserOverride** (`set_base_url_override` is per-process; persist to DB so multi-pod stays consistent).

---

## Row 8: Web UI (🟡)

### What's implemented (evidence)
- `frontend/Login.html`, `frontend/Platform.html`, `frontend/Game Console.html` — three Vite HTML entries (multi-page build, not SPA).
- `frontend/src/entries/login.jsx, platform.jsx, game-console.jsx` — entry mounts.
- `frontend/src/login-app.jsx` — auth UI.
- `frontend/src/platform-app.jsx` — library + cards + scripts tabs; sub-pages in `frontend/src/pages/cards.jsx, saves.jsx, scripts.jsx, settings.jsx`.
- `frontend/src/game-app.jsx`, `frontend/src/game-composer.jsx`, `frontend/src/game-panels.jsx`, `frontend/src/game-icons.jsx`, `frontend/src/game-console.css` — main gameplay screen.
- `frontend/src/console-assistant-panel.jsx`, `console-assistant-navigation.jsx` — in-game admin assistant.
- `frontend/src/branch-graph.jsx` — git-style branch viz.
- `frontend/src/api-client.js` — hand-rolled fetch wrapper that already speaks `/api/v1/*` (rewritten server-side to `/api/*` by `rpg-routes::rewrite_v1_prefix` middleware).
- `frontend/src/state-event-bridge.js` — SSE + WS subscription.
- `frontend/src/data-loader.js`, `frontend/src/markdown-render.jsx`, `frontend/src/responsive.jsx`, `frontend/src/web-vitals-rum.js`, `frontend/src/worldbook-status-toast.js`, `frontend/src/ui-atlas.js`, `frontend/src/motion.css`, `frontend/src/tokens.css`, `frontend/src/platform.css`.
- `frontend/src/components/` — shared atoms.
- `frontend/src/types/rust/` — 43 generated TS types (Row 4).
- `frontend/src/mock-data.js` — fixtures for offline UI tinkering.
- `frontend/vite.config.js` — proxy `/api → :7860`.
- `frontend/test-integration.sh`, `frontend/test-spark-and-merge.js`, `_test_*.js` — hand-run integration checks.

Server-side support:
- `rust/crates/rpg-routes/src/lib.rs:622-641` — `rewrite_v1_prefix` middleware (strips `/api/v1/` → `/api/`).
- `rust/crates/rpg-routes/src/ws.rs` (761 lines) — `/api/ws` WebSocket bidi bus (Wave 10-B).
- `rust/crates/rpg-routes/src/sse_events.rs` (250 lines), `sse_metrics.rs` (119) — named SSE events `hello/state_change/chunk/done/error`.

### Test coverage (evidence)
- No formal JS unit/visual regression tests in this repo. Only hand-run `_test_*.js` scripts.
- Server-side routes covered by `rpg-routes/src/{game,models,ws,...}.rs` inline tests (68 tests across `rpg-routes/src`).

### Stubs / TODOs / panics in this row
- No `todo!()` markers in `frontend/src/`.
- `_test_panel_all_tabs.js`, `_test_paneltimeline.js`, `_test_ui_atlas.js` suggest UI test patches are ongoing (manual harness).

### Invariants the migration AI must preserve
- Three independent HTML entries — **don't collapse to SPA**; auth/non-auth split is structural.
- Client always calls `/api/v1/*`. Server rewrites in middleware. If you bypass the rewrite (e.g. direct `app.merge`), you'll regress legacy paths.
- SSE event names are part of the wire contract — `hello / state_change / chunk / done / error` — must stay aligned with `rpg-routes::named_sse_event`.
- WebSocket `/api/ws` uses typed `WsClientMessage / WsServerMessage` (see `frontend/src/types/rust/events/`).
- Body limits: regular routes vs upload routes vs SSE routes are split (`build_routes / build_upload_routes / build_sse_routes`) — SSE routes exempt from `TimeoutLayer` and `GovernorLayer`.

### Concrete next steps to take this 🟡 → ✅
1. **Add a real frontend test harness** (Vitest + Playwright). The repo already has `.playwright-cli/` cached; wire it in.
2. **Codegen the api-client.js** from `rpg-routes`. Currently typed via hand maintenance.
3. **Polish mobile layout** (recent landing work mentioned "手机端 UI 全面修复" — apply same audit to main UI).
4. **i18n** — UI strings are inline Chinese. Extract.
5. **Accessibility pass**: no aria audit visible.

---

## Row 9: Public deployment / commercial license (❌)

### What's implemented (evidence)
- `deploy/` directory — Dockerfile, docker-compose, k8s manifests. Used internally.
- `LICENSE` (commit `36850d25`: chore: relicense placeholder MIT → Proprietary).
- README mentions a future dual-license (AGPL-3.0 + commercial) but neither is shipped.
- Landing page `landing-deploy/` (untracked; CF Pages target) + ECS02 reserve API (per memory file `project_play_landing.md`) — the waitlist surface.
- `CONTRIBUTING.md` says external PRs not accepted yet.

### Test coverage (evidence)
- N/A.

### Stubs / TODOs / panics in this row
- No code stubs; this is a business/legal/infrastructure row.

### Invariants the migration AI must preserve
- **Do not** add open-source license headers to source files until the dual-license is announced.
- Anything new shipped under `deploy/` should assume single-tenant for now (no multi-org RBAC, no billing).
- `LICENSE` file content is the authoritative copyright stance; don't replace with SPDX-only stubs.

### Concrete next steps to take this ❌ → ✅
1. Finalize commercial license text + AGPL-3.0 dual-license overlay.
2. Provision public Postgres+Redis (already in `deploy/docker-compose.yml`; needs hardened secrets management).
3. Ship per-tenant isolation: namespace `user_<id>/` directories already in place, but `UPLOAD_ROOT` etc. need quota enforcement (`rpg-platform/src/quota.rs` exists with 12 tests — leverage it).
4. Wire payment + plan tier into `usage.rs` cost accounting.
5. Public-beta criteria: branch row → ✅, script-pack public sharing → ✅, GoogleAI/Alibaba runtime backends → ✅, e2e tests green on every PR.

---

# Section A — Critical cross-cutting invariants

### Routing & AppState (`rpg-routes::AppState`)
- `rust/crates/rpg-routes/src/lib.rs:79-124` — `AppStateInner` holds every shared handle; `AppState = Arc<AppStateInner>` newtype with `Deref<Target=AppStateInner>` so handler code reads `s.db`, `s.state_store`, `s.llm_router` etc. Cloning `AppState` is a single Arc refcount inc.
- Inner mutability via `parking_lot::RwLock` (`llm_router`, `tool_registry`) — **never hold the lock across `.await`**. Acquire, snapshot/clone what you need, drop.
- `DashMap`-based fields (`gm_pool`, `stop_events`, `run_ids`, `console_conversations`, `console_pending_confirmations`, `chunk_uploads`, `health_cache`) are concurrent safe; entries created lazily.
- `AppConfig` is frozen at boot (`AppConfig::from_env`) and stored in `Arc` — env reads happen exactly once.
- `shutdown_token: CancellationToken` + `task_tracker: TaskTracker` — every `tokio::spawn`'d worker should select on `shutdown_token` and be tracked via `task_tracker.spawn(...)` for graceful drain.
- Router composition: three groups merged in `rpg-server/src/main.rs`:
  - `build_regular_routes()` → governor + timeout + body limit.
  - `build_sse_routes()` → exempt from timeout/governor (for long-lived SSE/WS).
  - `build_upload_routes()` → enlarged body limit.
- `rewrite_v1_prefix` middleware (`lib.rs:622-641`) lets the client speak `/api/v1/*` while handlers register `/api/*`.

### DB pattern (sqlx + pgbouncer transaction-mode)
- All write paths use sqlx `query`/`query_as` with positional `$N` binds, not named prepared statements (pgbouncer transaction-pool incompatible with persistent prepared names).
- Multi-statement transactions go through `pool.begin().await?` → `tx.commit().await?`. See `branches/deletion.rs:101-111` for canonical pattern.
- Non-DB side-effects (file unlinks, runtime activation) deliberately happen **outside** the transaction (idempotent / best-effort).
- `pgvector` is used for retrieval embeddings: migration `010_pgvector_columns_and_hnsw.sql` creates HNSW indexes; cosine distance is the default.
- Repo helpers (`rpg-db/src/repos/*.rs`) wrap row decoding into typed structs.
- Per-conn `statement_timeout` is 5000 ms; long pgvector scans must `set_statement_timeout` per tx.

### Event bus (`rpg-state::StateEventBus`)
- `tokio::sync::broadcast`, capacity 256. Lagged subscribers receive `RecvError::Lagged` — code must handle (typically: refetch state and resubscribe).
- Events are flat (`StateEvent::Updated { user_id, version } / OpApplied / Pending / TimelineJump`). They do NOT carry full state — subscribers re-read `StateStore::get(&user_id)` for the new snapshot.
- `rpg-routes/src/core.rs::api_state_events` (SSE) and `rpg-routes/src/ws.rs` are the two subscribers shipped today.
- `apply_op` (`rpg-state/src/ops.rs`) is the publish site. **All state mutations must go through `apply_op`** for event bus + audit_log + pending_writes coherency.

### Authentication
- Session storage: `sessions` table; tokens are hashed (migration `017_sessions_hashed_token.sql`).
- 14-day session lifetime (`SESSION_DAYS` in `rpg-platform/src/auth/sessions.rs:20`).
- `User` struct at `rpg-platform/src/auth/sessions.rs:25` uses typed `UserId`.
- `rpg-routes::require_user(state, headers)` (`lib.rs:420`) is the auth extractor. `user_id_or_anon` (`lib.rs:428`) falls back to `"anonymous"` for routes that allow it.
- Cookies are HttpOnly; bearer-token via `Authorization: Bearer` also accepted.
- Login rate limiter: `rpg-platform/src/auth/rate_limit.rs` — failure window + lockout via `infra::rate_limit::RateLimitBackend` (Memory or Redis based on `RPG_REDIS_URL`).
- CORS: `AppConfig.cors_origins` (comma-split env `RPG_CORS_ORIGINS`). Required in prod.

### Migration discipline
- `MIGRATIONS: &[…]` slice in `rust/crates/rpg-db/src/migrations.rs:80` is the single source of truth.
- **Append-only.** Adding a migration: write `migrations/025_*.sql`, add `static SQL_025 = include_str!(…)`, append `MigrationStep { id: 25, name: …, sql: SQL_025 }`.
- `pg_try_advisory_lock` (polling) at single fixed key serializes multi-pod startup. Do not change the key.
- `RPG_SKIP_AUTO_MIGRATE=1` skips runner — used in some test/dev flows.

### ts-rs generation pipeline
- Per-crate optional `ts-rs` feature; running `cargo test -p <crate> --features ts-rs` re-emits `.ts` files into `frontend/src/types/rust/…`.
- Crates that export: `rpg-schemas`, `rpg-state`, `rpg-llm`, `model_catalog`, `rpg-routes` (some derived types). Look for `bindings/` directories under each.
- Top-level state types → `frontend/src/types/rust/`. Wire/event types → `events/`. Catalog → `catalog/`.
- **No CI guard** that on-disk `.ts` matches source as of `main`. Adding one in CI is a recommended next step.

### ContextProvider registry
- Trait `ContextProvider` at `rpg-context/src/provider.rs:77` — `applies(ctx)` + `collect(ctx) -> Result<Vec<Layer>>`.
- `ProviderServices` (same file) is the DI bag (DB pool, embed fn, retrieve fn, timeline filter fn, module loader fn).
- Global registry: `static REGISTRY: LazyLock<RwLock<HashMap<String, Arc<dyn ContextProvider>>>>` (`rpg-context/src/registry.rs:13`). `register_provider` / `get_provider` are the entry points.
- Built-in providers (`rpg-context/src/providers/`): `rules.rs, memory.rs, novel.rs (4 providers), runtime_phase_digests.rs, module.rs (2 providers)`.
- New providers are registered at boot in `rpg-server::main` (search for `register_provider`).

### Provider routing chain (live → catalog → empty)
- `LlmRouter::pricing_for(api_id, model_id)` first checks catalog inline `ModelPricing`, then falls back to `BUILTIN_PRICING`.
- Catalog `refresh` (`model_catalog::Catalog::refresh`) — live API call → on failure, static catalog → on failure, empty `Vec`. Never `Err`.
- Backend resolution: `LlmRouter::register(api_id, Arc<AnyBackend>)` is called at boot; `AnyBackend` is the polymorphic wrapper over the 4 concrete backends.

---

# Section B — Code conventions

### Module structure pattern
- `<crate>/src/lib.rs` is documentation-first (//! header), with `pub mod` declarations and surgical `pub use` re-exports.
- Sub-features go into `<crate>/src/<feature>/{mod.rs, *.rs}` (see `rpg-platform/src/branches/`, `rpg-platform/src/auth/`, `rpg-platform/src/knowledge/`, `rpg-platform/src/runtime/`, `rpg-platform/src/script_import/`, `rpg-platform/src/infra/`).
- Tests inline (`#[cfg(test)] mod tests`) for fast feedback; cross-crate integration in `tests/` only when DB or HTTP needed.

### Error types
- **Typed errors per crate.** `thiserror::Error` enum at `<crate>/src/error.rs`:
  - `rpg-platform::PlatformError` (Validation / NotFound / Conflict / Forbidden / Unauthorized / RateLimited / Db / Serde / Io / Other(anyhow)).
  - `rpg-state::{StateError, OpError}`.
  - `rpg-llm::LlmError`.
  - `rpg-context::ContextError`.
  - `rpg-routes::ResponseError` (axum `IntoResponse` → JSON `{ok:false, detail, code}`).
- `anyhow::Error` is permitted only at integration boundaries (e.g., `rpg-server::main`) and inside `*::Other(anyhow::Error)` variants for last-resort wrapping.
- Standard error_codes: `bad_request / unauthorized / forbidden / not_found / conflict / not_implemented / internal_error` (`rpg-routes/src/lib.rs:235`).

### Async patterns
- `tokio` full features (`workspace.dependencies` in `rust/Cargo.toml`).
- All HTTP via reqwest with shared client built by `rpg-llm::pipeline::build_http_client`.
- Blocking work: none expected on the request path; CPU-bound dice/rules math is fast enough sync. SIMD JSON for LLM SSE hot-path (`rpg-llm::simd_parse`).
- Fire-and-forget: `tokio::spawn` for `schedule_llm_summary` and `spawn_embed_script`. **Track these with `task_tracker.spawn(...)`** for graceful shutdown (the existing code doesn't always — see Section D).
- Cancellation: `shutdown_token` should be selected against in long-running loops.

### Logging
- `tracing` crate; per-crate `tracing::{trace,debug,info,warn,error}` with field syntax (`tracing::debug!(save_id, rows=all.len(), "ensure_summaries 完成")`).
- Expected log levels:
  - `info`: lifecycle events (boot, shutdown, migration applied).
  - `debug`: per-request domain decisions (e.g., context provider chose layer N).
  - `warn`: degraded but recoverable (catalog refresh failed, falling back to static).
  - `error`: aborted operations.
- Default filter via `RUST_LOG=info,rpg_server=debug,sqlx=warn`.
- Prometheus metrics layered on top via `axum_prometheus::PrometheusMetricLayer` in `rpg-server::main`.

### Comments style
- File-level `//!` Chinese-first design doc that maps to the Python it replaces ("对应 Python `rpg/agents/gm/backends/anthropic.py`"). Keep this convention.
- Inline `//` comments mix Chinese/English freely.
- TODO markers use bracketed scope tags: `TODO[P2-LLM]`, `TODO[P2-SYNC]`, `TODO[Sonnet]`, `TODO[接入]`, `TODO[auth]`. Search by these to find category-grouped backlog.

### Newtypes
- `rpg-core::ids::{UserId, SaveId, RunId}` (`rust/crates/rpg-core/src/ids.rs:1-90`). All transparent `i64`, derive `sqlx::Type(transparent)` + `serde(transparent)`. **Use them at function boundaries** rather than raw `i64`.
- `String` is still used for user_id in `StateStore` (anonymous sentinel) — see Row 1 invariants.

---

# Section C — Test infrastructure

### How to run all tests
```bash
cd rust
cargo test --workspace          # all unit + crate integration tests (no DB needed)
cargo test --workspace --features ts-rs   # also regenerates frontend TS bindings
# e2e (requires docker):
docker compose -f rust/docker-compose.e2e.yml up -d
RPG_TEST_DB_URL=postgres://rpg:changeme@localhost:55432/rpg_e2e \
  cargo test -p rpg-server --features e2e -- --ignored
docker compose -f rust/docker-compose.e2e.yml down -v
```

### How tests use DB
- **No testcontainers** crate. **No `sqlx::test`**. Instead:
  - Unit tests that need a `PgPool` use `sqlx::PgPool::connect_lazy("postgres://localhost/nonexistent")` so the pool object exists for signature checks but no real connection is made (the test never `.await`s a query). See `rpg-platform/src/branches/*.rs` tests for pattern.
  - DB-touching integration tests live in `rust/crates/rpg-server/tests/e2e.rs`. Each test creates its own schema (`e2e_<nanoid>`), runs sqlx migrate against that schema, `DROP CASCADE`s on teardown.
- No in-memory Postgres (no `pgmock`, no `embedded-postgres`).

### Integration vs unit boundary
- Unit: `#[cfg(test)] mod tests` inline in each `.rs` file.
- Integration: `<crate>/tests/*.rs` (each is a separate binary). Most prominent: `model_catalog/tests/` (5 files, 31 tests) and `rpg-server/tests/e2e.rs` (gated).
- Benches: `rpg-llm/benches/`, `rpg-state/benches/`, `rpg-platform/benches/` (criterion, Wave 10-D).

### E2E status
- `rpg-server/tests/e2e.rs` (547 lines, 8 tests behind `#[cfg(feature = "e2e")] #[ignore]`):
  - `register + login`, `auth fail`, `state get`, `chat 401`, etc.
  - LLM calls are not mocked (the chat test asserts 401 when no API token configured).
- Browser/UI e2e: only `.playwright-cli/` (cache); no committed scripts.
- CI (`.github/workflows/ci.yml`) currently runs **Python** lints + tests (`ruff` strict, `mypy` informational, unittest `|| true`). **No Rust job runs in CI on `main`** as of the audited commit (the Python jobs were re-added post-rust-migration for landing-page lint cleanup). This is a significant gap.

---

# Section D — Known footguns

(Inferred from recent commits + comments + grep.)

### Multi-pod state coherence
- `LlmRouter` and `ToolRegistry` live in `parking_lot::RwLock` inside `AppState`. **They are per-process.** Cross-pod sync (e.g., a UserOverride base_url) requires DB-backed persistence; today it's per-process only (`model_catalog::Catalog::set_base_url_override`).

### `is_active` vs `active` column name
- `rpg-platform/src/branches/refs.rs:10` documents that earlier code wrote `active` instead of `is_active`. Look for stragglers when touching `branch_refs`.

### Stale module headers
- `rpg-platform/src/branches/mod.rs` describes seed / runtime / summary / maintenance / deletion as 骨架/TODO. Wave 14 filled them in. **The function bodies are the truth.**
- `rpg-platform/src/branches/tree_ops.rs:4` similarly stale.

### Embedding job has no terminal write
- `script_import/mod.rs:501` — `spawn_embed_script` doesn't write `import_jobs.status='done'` on success. UI may show indefinite "syncing" until polling logic times out.

### Fix-it bug commits in recent history
- `6076976f`: `runtime_checkouts.worker_id` column referenced but doesn't exist → tx rollback. If you touch checkout code, double-check column names against `019_runtime_checkouts.sql`.
- `ebf09957`: chat Phase 5 missed flush to DB + reload after activate. Pattern: after activating a save, **reload** `state_snapshot` into `state_store`, don't trust the in-memory copy.
- `7bd2c031`: same pattern for `activate_save`.
- `c49e1755`: chat Phase 4 only just got upgraded to `GameMaster::step()` (Wave 14). Earlier code paths might still exist that bypass the full tool loop — check `rpg-routes/src/game.rs::api_chat` thoroughly when extending.

### `sqlx::PgPool::connect_lazy` test pattern
- Tests use this so the pool is built without a real connection. **Any `.await` on a query in such a test will hang 60+ seconds** waiting for the bogus `localhost/nonexistent`. Don't add live queries to lazy-pool tests.

### Stop-signal cross-pod design (W6C)
- `cluster::is_stop_requested` polls the `stop_signals` table; `/api/stop` writes to it. If you add a long-running operation, periodically poll this with `(user_id, run_id)`.
- `AppState::next_run_id` allocates monotonic per-user run ids — store at request start, poll at boundaries.

### Provider rename migration (v024)
- `024_provider_rename.rs` (Wave 11.5-A) renamed some old `model_apis` rows. If you add provider rename logic, append a new migration; don't try to "consolidate" v024.

### `panic!` in non-test code
- `rpg-platform/src/crypto.rs:48` — `panic!` if key material is malformed at startup. **Intentional** — bad master key is unrecoverable. Don't catch this.

### Token-mode pgbouncer
- Never write `LISTEN/NOTIFY` SQL; never assume connection affinity. Even within one HTTP request, do all writes inside a single `pool.begin()` block — don't split DDL across multiple `pool.acquire()` calls.

### `RUST_LOG=info,sqlx=warn` in prod
- sqlx is verbose at `info`. Always demote to `warn` or noisy logs will dwarf real signal.

---

# Section E — Stale Python migration leftovers

### `rpg/` Python directory status
- Still on disk at repo root (`rpg/`, 60 entries). Active modules: `app.py`, `agents/`, `chat_pipeline.py`, `chapter_splitter.py`, `model_registry.py`, `platform_app/`, `context_providers/`, `context_engine/`, `core/`, `db/`, `modules/`, etc.
- `rpg/README.md` still describes the Python entrypoint (`uvicorn app:app --reload --port 7860`).
- `rpg/modules/ash_mine/` — the sole shipped scenario; the Rust code at `rpg-rules/src/modules.rs:159-240` loads it.
- CI (`.github/workflows/ci.yml`) runs `ruff` strict on `rpg/`; Python tests run with `|| true`.
- **Not imported by Rust at runtime.** The Rust workspace has zero `pyo3` / Python-FFI dependency. The `rpg/modules/<id>/module.json` files are the only Python-adjacent artifact the Rust code reads (and only as JSON).

### `python-legacy` branch
- Branch `python-legacy` exists (`git branch -a`). Recent commit log shows `d9af5566 revert: 撤回 main 上的 Python lint 修复` and `29.[completed] cherry-pick Python 修复到 python-legacy + revert main` in the task list — Python-only fixes are now landed on `python-legacy`, not `main`.
- Implication: `python-legacy` holds the last known-good Python implementation for reference. **Do not delete it; do not merge it back to main.**

### What still imports from old Python modules vs Rust
- Nothing in the Rust workspace imports Python. `rpg-server` is a pure Rust binary.
- The dev script `scripts/dev.sh` (in this repo) still launches Python uvicorn (per the `_pid_on_port` block and the `RPG_DIR="$ROOT/rpg"` path) — **outdated for the Rust runtime**; the Rust dev path is `cargo run -p rpg-server`. Either fix or document.
- `rpg/modules/ash_mine/module.json` and friends are read by Rust (`rpg-rules::modules::load_modules`); the Python source for the module remains the editorial source of truth.

### Cleanup recommendations
- Move `rpg/modules/<id>/` to `modules/<id>/` at repo root and update `RPG_MODULES_DIR` defaults — the modules aren't Python-specific and currently anchor the legacy directory.
- Delete `rpg/agents/`, `rpg/platform_app/`, `rpg/chat_pipeline.py` etc. once the Rust path has shipped a public release. Until then, they remain useful as the parity reference (every Rust file's `//!` doc-comment cites its Python sibling).
- Remove Python jobs from CI once the Rust port is the only build target.
- Replace `scripts/dev.sh` with a Rust-first dev orchestrator.

---

*End of audit. Total ~5,400 words. Migration AI: when in doubt, trust the function body over the module header, the migration list over the docstring, and `cargo test --workspace` over README claims.*
