# 后端地图(rpg/)

Python FastAPI 后端逐包/逐模块职责。给 AI 协作者:找「某功能住哪」先查这里,再跳代码。
标注 **[热路径]** = 每回合/每次导入都会走;**[别碰]** = 改动前必读约束。
权威真相源清单见根 `CLAUDE.md`,不在此重复。

## 入口与装配

- `app.py` — FastAPI 应用装配:建 `app`、注册 lifespan、`include_router` 挂载 `routes/` 与 `platform_app/api` 各路由。**体量大,历史上把 `/api/chat` 逻辑外迁到 `chat_pipeline/`、路由外迁到 `routes/`——新逻辑别再堆回 app.py。**
- `demo.py` — 本地 demo 启动辅助。
- `game_policy.py` / `game_state.py`(见 `saves/`) — 玩法策略常量。

## 回合链与 GM 服务

- `chat_pipeline/` **[热路径]** — `/api/chat` SSE 流水线。`__init__.py` 编排 5 个 async-generator phase;`context.py`(取上下文)→ `directives.py`(OOC/斜杠指令)→ `rules.py`(规则桥)→ `gm.py`(`run_gm_phase`,调 GM 出文)→ `persist.py`(落库)/ `postproc.py`(异步后处理)。`_common.py` 定义 `PipelineContext`/SSE 事件。搬家不改语义:事件名/顺序与旧 app.py 一致。
- `agents/gm/` **[热路径]** — 三贤者 GM 管线(司命/文宗/史官)。`master.py` 主编排,`backends/`(anthropic/openai_compat/_tiered 各 LLM 后端),`style_config.py`/`style_harness.py`(GM 文风),`stream_retry.py`(流式重试)。
- `gm_serving/` **[热路径]** — 回合服务层。`serve.py` 主服务,`context_inject.py`(上下文注入),`anchor_reconcile.py` + `anchor_signature.py`(锚点对齐,import `get_progress_window`),`impact.py`(后果),`steering.py`(引导强度),`settings.py`(含 `realign_progress_signals` 进度回退)。
- `context_engine/` — 分层上下文组装:`core.py`/`layers.py`/`projection.py`/`formatters.py`。
- `context_providers/` — 可插拔上下文 provider,`registry.py` 注册;含 `episodic_recall`(长程记忆)、`memory`、`npc_agenda`、`world_pulse`、`runtime_phase_digests` 等。加 provider 走 registry。

## Agents(LLM 微任务)

- `agents/_harness.py` — `call_agent_json_guarded`(结构化微任务护栏,强制 no_think 防思考黑洞)。**所有结构化 agent 调用的单点入口。**
- `agents/provider_errors.py` — `classify_provider_error`(供应商错误归类)。
- `agents/anchor_seed_agent.py` — 锚点播种 + `get_progress_window`(**玩家进度权威读取器**)。
- `agents/extractor.py` / `recorder.py` / `command_agent.py` / `context_agent.py` — 提取 / 记录 / 命令解析 / 上下文 agent。
- `agents/acceptance_verifier.py` — A/B 验收硬闸。`black_swan_agent.py` — 黑天鹅后处理(接 postproc_queue)。`world_heartbeat.py` — 离线活世界心跳。`worldbook_agent.py`、`phase_digest_agent.py`、`timeline_narrative_guard.py`、`save_history.py`、`anchor_seed_agent.py`。
- `agents/image_gen/` — 生图 agent。

## 工具 DSL / 命令

- `tools_dsl/command_dispatcher.py` — `ToolRegistry` + `ToolDispatcher`(**统一分发**:origin 白名单、整数纠偏、锁、限流、审计)。`get_registry()` 取进程单例。
- `tools_dsl/command_tools.py` — LLM-facing 工具 schema(纯数据 `COMMAND_TOOLS`)。`command_tools_register.py` — 启动时把工具包成 `ToolSpec` 注册进 registry。
- `tools_dsl/command_tools_*.py` — 按域拆分的工具实现(anchors/consequence/creative/image/imports/kb/misc/persona/phase/queries/rules/saves/tavern/ui_action/worldbook)。
- `tools_dsl/command_tools_script_write/` — 剧本写作工具,**已包化**(拆分收尾中):`chapters.py`/`anchors.py`/`canon.py`/`npc_cards.py`/`worldbook.py`/`extract.py`/`registry.py`。以完成后结构为准。
- `tools_dsl/chat_tool_router.py`、`set_parser.py`、`ui_dispatch_helper.py`、`tool_registry.py`(MCP catalog 持久化,与 dispatcher 的 registry 不同)。

## 核心工具层(core/)

- `core/outbound.py` — `safe_urlopen` / `safe_httpx_client`(**SSRF 安全出站,用户可控 URL/代理必走此**)+ `outbound_ua.py`(UA 注入)。
- `core/llm_backend.py` — 模型/API 解析:`resolve_preferred_model`/`resolve_preferred_api`/`guard_byok_usable`。
- `core/json_parse.py` — `parse_llm_json`(LLM JSON 容错解析)。
- `core/config.py` / `feature_flags.py` — 配置与 flag。`security.py`、`text_gates.py`(露骨内容门控)、`channel_fallback.py`、`request_cache.py`、`startup.py`、`version.py`、`vertex_sa.py`、`logging.py`。

## 平台层(platform_app/)

- `platform_app/perms.py` — **归属/权限权威判断**(`owns_save`/`script_readable`/`script_owned` + `require_*`)。
- `platform_app/user_credentials.py` — `resolve_api_key`(BYOK 凭据解析落地)。`user_models.py`/`user_cards.py`/`persona_skills.py`/`tavern_persona.py` — 用户资产。
- `platform_app/import_pipeline/` **[热路径]** — 剧本导入编排:`runner.py`(主)、`control.py`、`stages_core.py`/`stages_llm.py`(阶段)、`rebuild_*`(重建 worker/scheduler/registry)。
- `platform_app/script_import/` — 剧本导入触发 / 章节编辑编排(原 `script_import.py` 1443 行已按语义分段包化:`imports`(`import_script`/`preview_split`/正则校验)、`chapters`(章节 CRUD/合并/拆分/删除 + 负区两段式重排锁)、`sync_jobs`(无 BYOK 时零 LLM 的 `knowledge_sync` durable 任务 + `_SYNC_POOL`)、`uploads`(分片上传 + 跨平台 meta 文件锁)、`_base`(BASE 根 + 上传上限常量),`__init__` 薄门面 re-export 全部名并保留 `delete_script`/`resplit_script`)。
- `platform_app/db/` — **[别碰]** `migrations.py` 的 `MIGRATIONS` 是 **append-only**(version 单调递增,`_assert_migrations_monotonic` 守卫;绝不改旧条目)。`connection.py`(连接池)、`init.py`、`pgvector.py`、`utils.py`(分页/游标)。
- `platform_app/api/` — 平台 REST 路由。`frontend_routes.py` — 静态前端投喂。`scripts/`(原 `scripts.py` 1728 行已按资源族包化:`listing`/`chapters`/`cards`/`worldbook_canon`/`media`/`imports`/`library`/`overrides`/`review` 子模块共享 `_shared.router`,`__init__` 薄门面 re-export)供 `/api/scripts*`、`/api/uploads/*`。`script_edit/`(原 `script_edit.py` 1633 行已按资源族包化:`fork`/`versioning`/`sharing`/`worldbook`/`canon`/`anchors`/`writing`/`search`/`agent_doc` 子模块共享 `_shared.router`+`_shared._require_owner`/`_write_commit`,`__init__` 薄门面 re-export)供 MD 编辑器 fork/版本控制/手动编辑。`me/`(原 `me.py` 1537 行已按资源族包化:`profile`/`achievements`/`preferences`/`tasks`/`personas`/`cards_public`/`tavern`/`card_images`/`account`/`credentials` 子模块共享 `_shared.router`+`_shared._detect_image_mime`/`_store_imported_card_image`,`__init__` 薄门面 re-export)供 `/api/me/*`、`/api/achievements`、`/api/u/{username}/achievements`、`/api/cards/public`、`/api/gm-style/schema`(用户自助域:资料/偏好/凭据/用量/成就/角色卡/导出导入)。
- 其余:`auth.py`、`save_io.py`/`save_bundle.py`、`library.py`、`storage.py`(存储抽象,去硬编码根)、`assets_registry.py`、`postproc_queue.py`、`moderation.py`/`privacy.py`/`dmca.py`/`policy_notice.py`(合规)、`turnstile.py`、`achievements/`、`branches/`(分支/历史)、`knowledge/`。

## 路由(routes/)

`routes/` 的 FastAPI router 模块(`app.py` 装配):`game.py`(核心游戏)、`core.py`、`memory.py`、`permissions.py`、`models.py`、`worldline.py`、`worldbook_overlay.py`、`timeline.py`、`tavern.py`、`rath.py`、`rules.py`、`regex_scripts.py`、`sidebar.py`、`skills.py`/`persona_skills.py`、`mcp.py`、`console_assistant.py`。依赖注入在 `_deps.py`/`_deps_fastapi.py`。

## 知识库 / 提取 / 检索

- `kb/` — 存档级知识库:`save_kb.py`、`recall.py`(召回)、`canon_repo.py`/`live_repo.py`、`episodic.py`(情节记忆)、`world_scope.py`、`t0_seed.py`(存档 T0 种子)、`reveal.py`、`edges.py`/`alias.py`/`view.py`。
- `extract/` — 小说→事实提取管线:`pipeline.py`/`arc_pipeline.py`/`per_chapter.py`、`facts_refine.py`、`worldbook_enrich.py`、`resolve.py`(人名/语义确定性 resolve)、`dedup.py`、`embed.py`、`incremental.py`、`job_runner.py`、`world_key_backfill.py`。
- `ingest/` — 切分/清洗:`adaptive_split.py`、`sanitize.py`、`filters.py`。
- `retrieval.py` — RAG 检索(体量大,embedding 召回)。`chapter_splitter.py`/`chapter_fact_indexer.py` — 章节切分与事实索引。
- `character_card_generator.py` — 角色卡生成。`timeline_index.py`/`timeline_state.py`/`script_timeline.py` — 时间线索引/状态。

## 状态 / 规则 / 存档

- `state/` — 游戏状态核心:`core.py`、`json_ops.py`/`path_ops.py`(JSON 操作)、`consequence_ledger.py`(后果账本)、`npc_agenda.py`、`time_ops.py`、`regex_scripts.py`、`permissions.py`、`labels.py`、`_mixins/`。
- `state_repository.py`/`state_event_bus.py`/`state_write_context.py`/`state_op_tool_map.py` — 状态仓储/事件总线/写上下文。`save_phase_manager.py` — 存档阶段管理。
- `rules/` — 规则引擎:`engine.py`、`dice.py`、`dnd5e/`、`seed_policy.py`。`rules_bridge/` — 规则↔叙事桥:`intent.py`、`checks.py`、`combat.py`、`inventory.py`/`consume.py`、`entity_sync.py`、`suggest.py`、`module_ops.py`。
- `saves/` — 本地存档数据(`game_state.json`、`backups/`)。

## 模型层 / 供应商

- `model_registry.py` / `model_probe.py` / `model_aliases.py` / `model_catalog`(`config/model_catalog.json`) — 模型目录/探活/别名。
- `mcp_broker.py` — MCP 服务器代理。`redis_bus.py` — Redis 并发/跨 worker SSE(优雅降级)。

## 其它

- `rath/` — RATH 离线活世界:`engine.py`/`sim.py`/`briefing.py`。
- `console_assistant/` — 侧栏控制台助手(跨 save 资源管理,独立 origin):`llm_loop.py`、`write_preview.py`(预览/撤销)、`tools.py`、`streaming.py`。
- `schemas/` — Pydantic/数据契约(game/memory/models/permissions/sidebar/timeline/worldline…)。
- `bench/` — RPG harness 基准框架(可插拔指标 + judge)。见 `bench/README.md`。
- `cron/` — 定时任务。`scripts/` — 后端运维/回填脚本(`gen_openapi.py`、`run_postproc_worker.py`、backfill 系列)。
- `config/` — glossary、`mcp_servers.json`、`model_catalog.json`。`ui_manifest.py` — UI 清单。`skill_executor.py`、`persona_skills`(用户人格 skill)。
- `tests/` — `unit/`(≈200 文件,基线 0 failed)+ `integration/`(≈80)+ 顶层 e2e。跑法见根 `CLAUDE.md`。
