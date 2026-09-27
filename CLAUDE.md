# CLAUDE.md

给 AI 协作者的仓库导航。**加功能前先读本文件的「权威单一真相源清单」,别重复造轮子。**

## 一句话定位

多人 RPG / 角色扮演平台:把长篇小说拆成可玩剧本,由 LLM 驱动的 GM 管线跑回合制沉浸叙事。
后端 Python FastAPI(`rpg/`),前端 React(`frontend/`),另有 Electron 桌面壳、原生 SwiftUI iOS、Expo 移动端。
**main 分支永远是 Python**(历史上的 Rust 迁移已废弃;`scripts/dev.sh` 里的 Rust 注释是残留,忽略)。

## 顶层目录地图

| 目录 | 是什么 |
|---|---|
| `rpg/` | Python FastAPI 后端 + 所有 GM/KB/导入/LLM 逻辑。入口 `rpg/app.py`(装配路由 + lifespan) |
| `frontend/` | React 前端(Vite + Cloudscape)。源码在 `frontend/src/` |
| `desktop/` | Electron 桌面壳(渠道 B,electron-updater 自更新),独立 `package.json` 版本号 |
| `ios/` | 原生 SwiftUI 客户端(BYO-server),XcodeGen 工程 |
| `mobile/` | 独立 Expo / React Native app(`rpg-roleplay-mobile`),**与 `frontend/src/mobile/` 不是一回事** |
| `docs/` | 设计文档(`docs/design/`)、审计、runbook;本知识目录在 `docs/knowledge/` |
| `scripts/` | 顶层开发脚本:`dev.sh`(起 PG+后端7860+前端5173)、`setup.sh`、`bump_version.sh` |

深读:`docs/knowledge/backend-map.md`、`docs/knowledge/frontend-map.md`。

## 【加功能前必查——权威单一真相源清单】

**先查这里有没有现成的,有就用它,别手写平行实现。** 以下路径均已核实(2026-07)。

### 后端
- **归属/权限判断** → `rpg/platform_app/perms.py`:`owns_save` / `script_readable` / `script_owned`(+ `require_*` 抛异常版)。严禁手写归属 SQL。
- **玩家进度读取** → `get_progress_window`(权威定义在 `rpg/agents/anchor_seed_agent.py`,`gm_serving` 各模块 import 它)。**进度回退** → `rpg/gm_serving/settings.py` 的 `realign_progress_signals`。严禁直读 `worldline`/`timeline` 散落字段。
- **出站 HTTP(用户可控 URL/代理)** → `rpg/core/outbound.py`:`safe_urlopen` / `safe_httpx_client`。严禁裸 `httpx`/`urllib`(SSRF 防线)。凭据里配的代理一律经 `credential_proxy(resolved)` 取(服务器模式恒 None);`safe_httpx_client(` 每处调用必须显式写 `proxy=`(不需要的写 `proxy=None`,守卫 `test_outbound_proxy_parity`)。
- **LLM 工具调用分发** → `rpg/tools_dsl/command_dispatcher.py`:`ToolDispatcher` 统一做 origin 白名单 / 整数纠偏(`_coerce_declared_integers`)/ 锁 / 审计。新工具经 `get_registry()` 注册(见 `command_tools_register.py`),失败结果串遵循「`失败: /X ...`」惯例(识别规则见 dispatcher 顶部注释)。
- **结构化 LLM 微任务** → `rpg/agents/_harness.py` 的 `call_agent_json_guarded`(思考黑洞护栏,强制 no_think)。严禁裸调 agent。
- **LLM JSON 容错解析** → `rpg/core/json_parse.py` 的 `parse_llm_json`。
- **供应商错误分类** → `rpg/agents/provider_errors.py` 的 `classify_provider_error`。
- **模型/凭据解析** → `rpg/core/llm_backend.py`:`resolve_preferred_model` / `resolve_preferred_api` / `guard_byok_usable`;API key 落地 → `rpg/platform_app/user_credentials.py` 的 `resolve_api_key`。

### 前端
- **API 调用** → `frontend/src/api-client.js`(装 `window.api.*`,同源 Cookie 会话 + SSE helper)。别自己 fetch 后端。
- **localStorage** → `frontend/src/lib/storage.js`(`lsGet`/`lsSet`/`lsGetJSON`…)。
- **toast** → `frontend/src/toast.jsx` 的双总线:平台侧 `window.toast`、游戏侧 `window.__apiToast`(`createToastChannel` + `setWindowToast`/`setApiToast`)。别自己 mount toast stack。
- **模型选择器** → `frontend/src/components/AgentModelPicker.jsx`(按需 LLM 增强统一走它)。

## 模块地图速览(包化后结构)

- **回合链(热路径)** `rpg/chat_pipeline/`:`/api/chat` 的 SSE 流水线,5 个 phase 依序(`directives` → `context` → `rules` → `gm` → `persist`,旁路 `postproc`),核心生成器 `run_gm_phase`。
- **GM 管线** `rpg/agents/gm/`(三贤者:司命/文宗/史官,`master.py`)+ `rpg/gm_serving/`(上下文注入/锚点对齐/后果)。
- **上下文** `rpg/context_engine/`(分层组装)+ `rpg/context_providers/`(可插拔 provider,注册在 `registry.py`)。
- **知识库** `rpg/kb/`(存档 KB / 召回 / world_scope)+ `rpg/extract/`(小说→事实提取管线)+ `rpg/ingest/`(切分/清洗)。
- **导入链(热路径)** `rpg/platform_app/import_pipeline/`(剧本导入 stage 编排,`runner.py`)。
- **工具 DSL** `rpg/tools_dsl/`(命令工具按域分文件 `command_tools_*.py`;`command_tools_script_write/` 已包化)。
- **路由** `rpg/routes/` + `rpg/platform_app/api/`;`rpg/app.py` 用 `include_router` 装配。
- **前端** `frontend/src/`:`pages/`(整页)、`components/`(含 `platform/`、`scripts/`、`settings/`、`mobile/`)、`hooks/`、`lib/`、`i18n/`;多入口 `entries/`(platform/game-console/tavern/login)。

## 工程铁律(精选)

- **根因 > 散落守卫**:重复加拦截是打地鼠。停手找根因,一条 migration 去约束胜过十处 guard。
- **确定性代码缝**:修复必须落在确定性代码里,不准指望提示词让 LLM「记得遵守」。中文语义信号别用单字符判定(宁漏勿误)。
- **「UI 存在 ≠ 生效」**:加设置项前先 grep 后端有没有真读那个 pref,否则只是装饰。
- **防误合**:语义相似 ≠ 可合并。以行为 / DOM / 集合等价为闸,合并前确认无副作用差异。
- **同面横扫(修 A 必查孪生)**:修 bug / 加护栏 / 改契约时,必须 grep 平行实现端并**同一批次**对齐,或在 commit 里注明豁免理由。已知平行族:①四端回合操作(entries/game-console ↔ pages/tavern ↔ mobile/game/MobileGame ↔ mobile/pages/MobileTavern,共享缝 lib/tavern-chat-run.js)②GM 三后端 `agents/gm/backends/{openai_compat,vertex,anthropic}.py`(共享缝 _tiered/_effort/provider_errors)③回滚 delete(`branches/deletion.py`)↔ fork(`branches/tree_ops.py`,共享缝 `_opening_offset_from_history`)④桌面/移动图标字典(共享缝 `lib/icon-paths.jsx`)⑤酒馆绑卡 persona↔character(共享缝 `tavern_persona.apply_persona_card_to_chat(role=)`)。无法合并的刻意变体,首选加**孪生奇偶守卫测试**(范例 `frontend/src/__tests__/icon-paths-parity.test.js`)锁死同步义务。
- **别碰清单**:`rpg/platform_app/db/migrations.py` 的 `MIGRATIONS` **append-only**——只加新条目(version 单调递增),绝不改已发布的旧条目。
- **页面外壳模式**:`pages/settings.jsx`、`pages/scripts.jsx`、`platform-app.jsx` 已拆薄(实现住 `components/settings/`、`components/scripts/`、`components/platform/`),页面文件只做路由/转发 export——新组件跟着住对应 components/ 子目录。
- **测试跑法**:后端 `./rpg_env/bin/python -m pytest rpg/tests/unit -q`(基线 0 failed);前端 `cd frontend && npm run build && npm test`(vitest)。
- **版本号**:PATCH=bug / MINOR=功能 / MAJOR 只有用户拍板;desktop `package.json` 单独 bump;日常修复不打 tag。
- **文档维护**:模块搬家/新增子系统,**同一 commit** 更新对应 map(规约见 `docs/knowledge/README.md`)。

## 硬约束

- 本仓库同步公开 OSS。**严禁**在代码或文档写入:服务器地址 / SSH / 凭据 / cookie / 生产运维细节 / 用户数据 / 小说正文。运维知识不属于这套文档。
