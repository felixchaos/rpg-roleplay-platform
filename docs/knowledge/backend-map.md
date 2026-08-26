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
- `context_engine/` — 分层上下文组装:`core.py`/`layers.py`/`projection.py`/`formatters.py`;`budget.py`(层预算全局求解 —— 纯函数,按 min/want/priority 在模型窗口内分配,宽裕时等价于每层拿满 want);`_constants.py` 的 `MAX_LAYER_CHARS` 是**层预算登记表**,漏登记会被静默截断到默认上限,守卫 `tests/unit/test_context_layer_budget_registry.py`。
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
- `tools_dsl/chat_tool_router.py`(**直发工具窗口的档位单一真相源** —— `classify_tool()` 决定哪些工具的完整 schema 直发给模型;窗口是硬名额,排在窗口外约等于不存在。新工具必须落进某个显式档,守卫 `tests/unit/test_tool_window_tiering.py`)、`set_parser.py`、`ui_dispatch_helper.py`、`tool_registry.py`(MCP catalog 持久化,与 dispatcher 的 registry 不同)。

## 核心工具层(core/)

- `core/outbound.py` — `safe_urlopen` / `safe_httpx_client`(**SSRF 安全出站,用户可控 URL/代理必走此**)+ `outbound_ua.py`(UA 注入)。
- `core/llm_backend.py` — 模型/API 解析:`resolve_preferred_model`/`resolve_preferred_api`/`guard_byok_usable`。
- `core/json_parse.py` — `parse_llm_json`(LLM JSON 容错解析)。
- `core/text.py` — 文本工具权威缝:`slugify`(URL/目录/文件名安全化,保留中文,`fallback` 参数化)/ `normalize_for_fp`(指纹归一化,去标点空白只留文字数字)。散落的 `_slugify`/`_normalize_for_fp` 统一委托此处。
- `core/clock.py` — 时间戳权威缝:`now_iso()`(本地时间、秒级 ISO 8601)。散落的 `datetime.now().isoformat(timespec="seconds")` 统一走它。
- `core/vecmath.py` — 向量数学权威缝:`cosine(a, b)`(余弦相似度,零向量返回 0.0)。散落的 `ingest/filters.py`/`extract/resolve.py` 各自的 `_cosine` 统一委托此处(本地名保留)。
- `core/config.py` / `feature_flags.py` — 配置与 flag。`deployment_mode_normalized`/`is_local_deployment_mode`/`LOCAL_MODES`(部署模式权威缝,散落的 `{"local","desktop","self_hosted","self-hosted"}` 判定统一引用)。`security.py`、`text_gates.py`(露骨内容门控)、`channel_fallback.py`、`request_cache.py`、`startup.py`、`version.py`、`vertex_sa.py`、`logging.py`。

## 平台层(platform_app/)

- `platform_app/perms.py` — **归属/权限权威判断**(`owns_save`/`script_readable`/`script_owned` + `require_*`)。
- `platform_app/user_credentials.py` — `resolve_api_key`(BYOK 凭据解析落地)。`user_models.py`/`user_cards.py`/`persona_skills.py`/`tavern_persona.py` — 用户资产。
- `platform_app/import_pipeline/` **[热路径]** — 剧本导入编排:`runner.py`(主)、`control.py`、`stages_core.py`/`stages_llm.py`(阶段)、`rebuild_*`(重建 worker/scheduler/registry)。
- `platform_app/script_import/` — 剧本导入触发 / 章节编辑编排(原 `script_import.py` 1443 行已按语义分段包化:`imports`(`import_script`/`preview_split`/正则校验)、`chapters`(章节 CRUD/合并/拆分/删除 + 负区两段式重排锁)、`sync_jobs`(无 BYOK 时零 LLM 的 `knowledge_sync` durable 任务 + `_SYNC_POOL`)、`uploads`(分片上传 + 跨平台 meta 文件锁)、`_base`(BASE 根 + 上传上限常量),`__init__` 薄门面 re-export 全部名并保留 `delete_script`/`resplit_script`)。
- `platform_app/workspace/` — 存档工作区:存档/剧本列表·概览·创建·初始快照(原 `workspace.py` 1280 行已按职责包化:`listing`(`overview`/`scripts(_page)`/`saves(_page)`/`save_detail` + `ensure_default` 遗留存档 backfill 兜底 + `_readiness_for_scripts` 就绪度计数)、`snapshot`(**⚠️出生点/进度信号病灶**:`_build_initial_snapshot` 写 `worldline.progress_chapter` + `_apply_script_opening` 从出生锚点章派生情境 + `_scrub_berlin_default` 清 DEFAULT_STATE 硬编码)、`creation`(`create_save`/`create_tavern_save` + `_seed_kb_at_creation` 创建即 seed KB,初始 state 委托 `snapshot._build_initial_snapshot`),`__init__` 薄门面 re-export 全部名;`workspace.create_save`/`ensure_default` 是既有 monkeypatch 目标,消费方走门面属性查找)。
- `platform_app/db/` — **[别碰]** `migrations.py` 的 `MIGRATIONS` 是 **append-only**(version 单调递增,`_assert_migrations_monotonic` 守卫;绝不改旧条目)。`connection.py`(连接池)、`init.py`、`pgvector.py`、`utils.py`(分页/游标)。
- `platform_app/api/` — 平台 REST 路由。`frontend_routes/`(原 `frontend_routes.py` 1136 行 ⚠️名不副实的历史杂烩[认证/资料/账号/存档/搜索/模型/管理等多域路由,起因是避免与 api.py 合并冲突的权宜],2026-07-15 已按域包化:`auth`/`profile`/`account`/`saves`/`cards`/`models`/`search`/`admin` 子模块共享 `_shared.router`+`_shared._bad`/`_client_ip`,`__init__` 薄门面逐名 re-export[含 `_ensure_profile_extras_table`/`_storage_store_bytes` 等外部引用名 + 原顶层 import 名];**⚠️历史杂烩仅拆文件未治理**——绝未改任何路由 path/method,端点未按语义「归位」,归属治理是另一议题)。`scripts/`(原 `scripts.py` 1728 行已按资源族包化:`listing`/`chapters`/`cards`/`worldbook_canon`/`media`/`imports`/`library`/`overrides`/`review` 子模块共享 `_shared.router`,`__init__` 薄门面 re-export)供 `/api/scripts*`、`/api/uploads/*`。`script_edit/`(原 `script_edit.py` 1633 行已按资源族包化:`fork`/`versioning`/`sharing`/`worldbook`/`canon`/`anchors`/`writing`/`search`/`agent_doc` 子模块共享 `_shared.router`+`_shared._require_owner`/`_write_commit`,`__init__` 薄门面 re-export)供 MD 编辑器 fork/版本控制/手动编辑。`me/`(原 `me.py` 1537 行已按资源族包化:`profile`/`achievements`/`preferences`/`tasks`/`personas`/`cards_public`/`tavern`/`card_images`/`account`/`credentials` 子模块共享 `_shared.router`+`_shared._detect_image_mime`/`_store_imported_card_image`,`__init__` 薄门面 re-export)供 `/api/me/*`、`/api/achievements`、`/api/u/{username}/achievements`、`/api/cards/public`、`/api/gm-style/schema`(用户自助域:资料/偏好/凭据/用量/成就/角色卡/导出导入)。`admin/`(原 `admin.py` 1463 行已按资源族包化:`users`/`usage`/`audit`/`health`/`logs`/`registration`/`security`/`maintenance`/`dmca`/`csam`/`aup`/`allowlist`/`co_builders`/`achievements` 子模块共享 `_shared.router`+`_shared._require_admin`/`_get_app_config`/`_set_app_config`/`_write_audit`,各族配置常量(`_*_CFG_KEY`/`_DEFAULT_*`/`_ACHV_*`)与其读写方同居,`__init__` 薄门面 re-export)供 `/api/admin/*`(需 admin 角色)+ `/api/internal/allowlist/bulk`(跨服务共享 secret 认证)。
- 其余:`auth.py`、`save_io.py`/`save_bundle.py`、`library.py`、`storage.py`(存储抽象,去硬编码根)、`assets_registry.py`、`postproc_queue.py`、`moderation.py`/`privacy.py`/`dmca.py`/`policy_notice.py`(合规)、`turnstile.py`、`achievements/`、`branches/`(分支/历史)、`knowledge/`。

## 路由(routes/)

`routes/` 的 FastAPI router 模块(`app.py` 装配):`game/`(核心游戏 · SSE 热路径;原 `game.py` 1327 行已按资源族包化:`new`/`opening`/`chat`/`saves` 子模块共享 `_shared.router`+跨族 helper `_shared._client_safe_error`/`_note_channel_health_failure`/`_sanitize_payload`/`_log`,`__init__` 薄门面 re-export;供 `/api/new`、`/api/opening`、`/api/chat*`、`/api/stop`、`/api/save`、`/api/message/edit`、`/api/acceptance/choice`)、`core.py`、`memory.py`、`permissions.py`、`models.py`、`worldline.py`、`worldbook_overlay.py`、`timeline.py`、`tavern.py`、`rath.py`、`rules.py`、`regex_scripts.py`、`sidebar.py`、`skills.py`/`persona_skills.py`、`mcp.py`、`console_assistant.py`。依赖注入在 `_deps.py`/`_deps_fastapi.py`。

## 知识库 / 提取 / 检索

- `kb/` — 存档级知识库:`save_kb.py`、`recall.py`(召回)、`canon_repo.py`/`live_repo.py`、`episodic.py`(情节记忆)、`world_scope.py`、`t0_seed.py`(存档 T0 种子)、`reveal.py`、`edges.py`/`alias.py`/`view.py`。
- `platform_app/knowledge/` — 剧本/存档 KB 仓储 + 检索 + 向量嵌入层(区别于顶层 `kb/` 的存档运行时 KB)。`__init__.py` 是公共 API 薄门面(re-export `sync_script_knowledge`/`ensure_game_session`/`retrieve_*`/`list_*` 等,含私有 `_ensure_book`/`_chunk_text` 供 import_pipeline)。仓储族:`character_cards.py`+`_character_cards_repo.py`、`worldbook.py`+`_worldbook_repo.py`、`worldline.py`+`_worldline_repo.py`、`context_runs.py`+`_context_runs_repo.py`、`memory.py`+`_memory_repo.py`、`session.py`+`_session_repo.py`(`sync_script_knowledge`/`ensure_game_session`)。`_search.py` — 检索热路径(vector 余弦 + BM25-like 双路,`_embed_query` 按建库 (api_id,model) 强制一致召回;进程内 `_SCRIPT_EMBED_META_CACHE`/`_UNBOUND_EMBED_WARNED`)。切块/同步:`_chunks.py`(chunk/document upsert)、`_sync.py`(`_ensure_book`)、`_utils.py`(`_chunk_text`)、`_constants.py`(CHUNK_CHARS/OVERLAP)、`_pin.py`(固定记忆)。`retrieval.py`(`retrieve_runtime_context`/`retrieve_script_context`)、`script_pack.py`(剧本包组装)、`script_overrides.py`、`card_audit.py`(角色卡审计)、`llm_extract.py`。
  - `embedding/` — 向量嵌入子包,**已按供应商包化**(2026-07 拆分自单文件 `embedding.py` 1038 行,纯机械搬家零行为变化):`__init__.py`(公共层门面 — OpenAI 兼容通道 `_embed_via_openai` + dispatch/config 解析/`embedding_preflight`/`embed_query` + 共享错误态 `_last_openai_embed_error` + reload 敏感的 `DEFAULT_EMBED_API_ID`;测试在包命名空间 patch `_embed_via_*`/`_resolve_embed_config`,故这些逐字定义于此,globals 在门面解析)、`_base.py`(叶子:常量/维度 `EMBED_DIM`/`provider_lacks_embedding`/`_is_admin`/`_vec_literal`)、`_vertex.py`(Vertex genai + client 缓存)、`_gemini.py`(原生 embedContent + 地区封禁自愈 `_GEO_BAN_CACHE`)、`_cohere.py`、`_writer.py`(后台 batch 作业 `embed_script`/`embed_status`/`_embed_chunks_loop*` + 运行锁 `_EMBED_QUEUE_RUNNING`/Redis;与 `__init__` 构成受控有序循环导入)。门面 re-export 全部名(`from platform_app.knowledge.embedding import embed_query/embed_script/embed_status/embedding_preflight/_embed_batch/_vec_literal/provider_lacks_embedding/has_platform_fallback_role` 零改动)。可变全局与其读写方同居(cache/pool/geo-ban/queue 各归其模块)。
- `extract/` — 小说→事实提取管线:`pipeline.py`/`arc_pipeline.py`/`per_chapter.py`、`facts_refine.py`、`worldbook_enrich.py`、`resolve.py`(人名/语义确定性 resolve)、`dedup.py`、`embed.py`、`incremental.py`、`job_runner.py`、`world_key_backfill.py`。
- `ingest/` — 切分/清洗:`adaptive_split.py`、`sanitize.py`、`filters.py`。
- `retrieval/` — GM 上下文检索热路径(rail 原著注入/进度窗口/防剧透闸),已包化。薄门面 `__init__.py` re-export 全部名(`import retrieval`/`from retrieval import retrieve_context` 零改动);`_common.py`(log+BASE/路径常量)、`defaults.py`(默认剧本判定+泄漏过滤)、`progress.py`(进度窗口族)、`anchor_prose.py`(rail 原文注入族)、`sources.py`(RAG 召回族:bm25/摘要/facts/worldbook/角色卡)、`assemble.py`(组装入口 `retrieve_context`;源码断言测试读此文件)。`chapter_splitter.py`/`chapter_fact_indexer.py` — 章节切分与事实索引。
- `character_card_generator.py` — 角色卡生成。`timeline_index.py`/`timeline_state.py`/`script_timeline.py` — 时间线索引/状态。

## 状态 / 规则 / 存档

- `state/` — 游戏状态核心:`core.py`、`json_ops.py`/`path_ops.py`(JSON 操作)、`consequence_ledger.py`(后果账本)、`npc_agenda.py`、`time_ops.py`、`regex_scripts.py`、`permissions.py`、`labels.py`、`_mixins/`。
  - `state/phase_digest_policy.py` — phase digest 的**归属划分单一真相源**:哪些 phase 归「已发生历史摘要」层、哪些进 `history_messages()` 的前情提要,以及两条路各自的上限。两边必须 import 它,否则同一段历史会在一个请求里注入两次(守卫 `tests/unit/test_phase_digest_ownership.py`)。
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
