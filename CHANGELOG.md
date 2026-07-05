# Changelog

All notable changes to RPG Roleplay are documented here.

Format adapted from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version scheme: **SemVer** `MAJOR.MINOR.PATCH[-channel.N][+build]` since `v0.5.0` (single source of truth: root `VERSION` file; bump via `scripts/bump_version.sh`). A new DB migration bumps at least MINOR. Historical `0.x-waveN` entries below are kept as-is.

---

## [Unreleased]

## [1.38.0] - 2026-07-05

### Added
- **Vertex SA 前置校验(新手引导头号坑收口)**:新用户默认模型是需上传 SA JSON 的 Agent Platform(Vertex),此前选模型/建存档全程零校验、发第一条消息才报「未找到 Service Account」。现在 POST /api/models/select 与 POST /api/saves(按 _ensure_loaded 同优先级链解析实际 api_id)在生产鉴权模式下命中 vertex_ai 且无 SA 即 400 + needs_model_config + 引导文案;本地模式/解析失败保守放行,admin 豁免(同 _redact_catalog 约定)。AgentModelPicker 的 models_select 错误不再静默吞。


## [1.37.1] - 2026-07-05 (@ 90fefef83)

### Fixed
- **SSE 断连后 done 补发触发 `RuntimeError: async generator ignored GeneratorExit`(生产每天 7-13 次 asyncio Task exception)**:`_stream_with_done_guard` 在 finally 无条件 yield 补发 done,客户端断开时 Python 已注入 GeneratorExit、继续 yield 属非法。修=照抄 `console_assistant/streaming.py` 已验证防护(except GeneratorExit 置位后 raise,finally 仅未断连时补发);正常/内部异常路径行为不变(「转圈不清」修复无回归)。
- **404 模型不存在 / 400 功能未开启 穿透错误分类落「本轮处理出错,请重试」**(生产实况:中转站 `Function ... Not found for account`、Gemini `Developer instruction is not enabled`):`classify_provider_error` 新增 `model_unavailable`(404 或 model_not_found / not found for account / does not exist)与 `feature_unsupported`(400 + "is not enabled for")两类,给「重试无法恢复,请换模型」可行动文案;放在既有五类之后不改变现有分类。单测 8 条含 context 优先级回归守卫。
- **剧本版本回滚按钮可点但后端是 501 stub(「UI 在 ≠ 生效」)**:三端(游戏台版本下拉 / 桌面版本历史抽屉 / 移动端)按钮置灰 + 「版本回滚暂未开放」提示(中英 i18n),后端不动,待 payload chain 回放真实现后启用。
- **前沿模式下发散局进度冻死 / 越走越偏(行者无疆:半天不触发锚点,GM 反复判「时间场合不对→忽略」)—— 违背「确定性不得绕过三贤者」原则的回退**:排查其 268 号档(无限恐怖异形1副本,turn 503 卡在第 7 章、22 个 occurred 之后 437 回合零推进)定位到根因是我在 O/S7 前沿迁移里做过了头 —— ①`anchor_reconcile` 的 `est_on = ... and not _frontier_on` 在前沿模式下**关掉了史官的估章/`progress_motion` 兜底**;②`settings.read_settings` 把史官的 `progress_chapter` **降级为 legacy、只用纯确定性 `max(floor, derived)`(已到达锚点派生)**。两处等于让确定性代码把进度判断从三贤者(史官)手里拿走 → 玩家发散到锚点命中不了时,进度只认「真·到达」→ 冻死。**修正(判断还给史官、确定性退回当护栏)**:前沿模式下史官估章/motion 照常生效(`_apply_pace_fallback` 的「有没有前进是 LLM 判断、累计/`pace_cap`/`clamp`/单调是确定性护栏」范式);`read_settings` 取 `max(floor, derived, 史官进度)`。前沿退回只当【揭示护栏】(reached 地板防剧透),不再当唯一进度源。真库复现:冻档发散 6 回合 → 史官 motion 推进 7→11(有界不过冲、motion=0 不推进);生产实测 frontier 档 `progress_chapter` 与 reached floor 差≤1(无旧过冲残留,纳入 max 安全);anchor/progress/reveal/frontier 全套 100 passed(两处 enshrine 旧绕过的测试改为断「前沿不再绕过史官」)。
- **acceptance A/B「改写」变成了续写下一段(行者无疆:当前版末尾『传来一声尖叫』、改写版开头顺着那声尖叫往下写)**:根因=改写候选用 `respond_stream_with_tools` 把改写指令追加到【当前 `state.history`】之上,而 Phase 5 `record_turn` 已把[玩家行动 + 首稿]写进历史 → 模型把改写指令当成**新回合**、续写首稿末尾,而不是重写本轮。修:改写候选改为【首稿时的历史快照 + 玩家行动 + 首稿作为「待改写对象」】**文本直调 backend**(新 `_rewrite_candidate_text`),指令明确「改写替换、不是续写」;async 后台任务在 `record_turn` **之前**快照历史再交出(运行时历史已被污染)。真库 e2e 断言:改写消息里首稿【不】作为 assistant 历史出现、只作为待改写对象在最后一条 user 指令内 + 指令含「不是续写」;acceptance 全套 72 passed。
- **供应商 5xx / 网关宕机时错误文案含糊,被误当平台故障**(行者无疆报「生成失败(错误码 Ecxxxx)」,实为其中转站 `opencode.ai/zen` 返回 502 Bad Gateway / Cloudflare origin 过载):`classify_provider_error` 新增 `upstream` 类——HTTP 5xx 或 message 命中网关特征(cloudflare / bad gateway / service unavailable / origin_bad_gateway)时,给明确一句「你的模型服务暂时不可用(返回 5xx 网关错误,多为供应商/中转站过载或宕机),不是平台或存档的问题。请稍等重试或换个模型/供应商」。放在 4xx 各类之后,普通 400 / 未知仍走原兜底(不误吞)。单测覆盖 502/503/msg-only 命中 + 4xx 各类无回归。

### Added
- **正则脚本(SillyTavern regex parity,反馈#93 之三)**:全新用户自定义正则,对 **AI 输出**做确定性 find→replace(v1 仅输出/显示作用域;输入作用域与指令解析纠缠,留 v2,故 UI 不给假开关)。`/api/regex/scripts`(增删查,存 `user_preferences.regex_scripts`,服务端校验正则可编译)+ `state/regex_scripts.apply_output_regex`(chat_pipeline 清洗后应用)+ 可复用组件 `RegexScriptsSection`(酒馆抽屉新增「正则」页签)。替换串用 SillyTavern/JS 风格 `$1`/`$&`(手动展开,避 Python 反斜杠陷阱),flags 支持 i/m/s。**ReDoS 安全**:嵌套无界量词(如 `(a+)+`)启发式在**保存 + 应用**双端拒绝 + daemon 线程 wall-clock 超时(0.5s)兜底 + 输入长度上限,绝不断轮/不挂进程。真库 e2e:捕获组/删除/flags/停用/无效跳过/ReDoS 拦截 全通。
- **酒馆世界书「添加入口」(反馈#93 之一)**:酒馆等无剧本存档的世界书全靠 `save_worldbook_overlays` 的 addition,此前只有 LLM/命令能加、前端无 UI 入口。新增 `/api/worldbook/overlay`(GET 列表全文 / POST 新增走既有 `worldbook_add` 工具 ui_button origin / POST remove 归属校验删 addition)+ 可复用组件 `WorldbookOverlaySection`(游戏台 PanelWorldbook + 酒馆抽屉新增「世界书」页签 + 移动世界书面板,单一来源)。真库 e2e:add→list→remove 全通。

### Fixed
- **生成参数预设从不生效(反馈#93 之二)**:设置页早有 temperature/top_p/top_k/惩罚项 + 4 档预设 UI,值落 `user_preferences` 顶层扁平键,但**后端从不读取**(各 provider 硬编码 temperature)→ 调预设等于没调。新增单一来源 `agents/gm/backends/_gen_params.resolve_gen_params(user_id)`(**只返回用户显式设过的键** + 校验夹取 → 未配置用户零行为变化),三后端**叙事**调用接入:openai_compat(temperature/top_p/penalties + extra_body 的 top_k/repetition_penalty,自愈从「只挡 temperature」扩到「拒任一采样参数即剥全部退默认」)、vertex(temperature/top_p/top_k 覆盖)、anthropic(temperature/top_p/top_k;Extended Thinking 开启时全跳过,避冲突)。结构化/JSON 调用保持低温不受影响。真库 e2e:未配置空 / 设置生效 / 超界夹取 / 部分设置只回设过的键 全通。
- **酒馆系统提示词无法保存(反馈#94)**:`POST /api/tavern/chats/{id}/system-prompt` 仅写 `game_saves.state_snapshot`,但主读源是 `runtime_checkouts.state_snapshot`(`load_active_state` 优先读它),且 kb_native 档(生产 74 个酒馆档中 49 个)经 `_kb_backed_state`→`materialize` 从 `kb_worldline_vars` 重建 `tavern` 覆盖该写 → 系统提示词保存后回退。**两处根因修复**(均沿用既有范式):①端点在本对话即活跃档时把 `system_prompt` 写进 working-tree state + `_persist_runtime_checkpoint`(runtime + `snapshot_hash` bump,跨 worker 失效),与 worldline 变量端点同款;②`_kb_backed_state` 对 `tavern` 子树做 blob 覆盖(同 `session_model`,out-of-turn 编辑不随回合 import 进 KB)。真库 e2e 往返验证(kb_native 档 set→reload 持久化 + 证伪旧路径);tavern/materialize/kb 测试 13 passed。
## [1.36.0] - 2026-07-02 (@ 81bb5f986)

### Added / Changed
- **OOC/指令(`/set` 等)前端适配对齐各端**(用户审计:web 游戏台完整,但酒馆/移动缺回执、独立酒馆/移动酒馆缺斜杠菜单、/set 管理面板 web 独有)。补齐(单一来源、不 fork):
  - **命令回执**:`lib/tavern-chat-run.js`(pages/tavern + tavern-app + MobileTavern 共用)补 `on_updates`(pre_llm)+ `on_system_receipt` → toast「设定已更新」+ `gotReceipt` 守卫(纯 `/set` 轮不再误判「空回复」恢复草稿)。移动游戏本就复用 game-console run-loop 已有回执。
  - **斜杠菜单**:独立酒馆(tavern-app)去掉 `hideSlash` + 接 `onSlashPick`(挑命令塞前缀);移动酒馆(MobileTavern)加斜杠 sheet;命令集**单一来源** = 共享 `SLASH_COMMANDS`(MobileGame 不再自带子集,补齐 `/memory` `/permission` 全 13 条)。
  - **`/set` 管理面板**:移动端 StatusPanel **复用**电脑端 `ForcedSetSection` 组件(列 `worldline.user_variables` + 逐条删 + 清空),而非另写一份。
  - (iOS 的回执/管理面板/命令对齐单独一批。)

## [1.35.1] - 2026-07-02 (@ a80ef39d8)

### Changed
- **确定性叙事纠错收拢到一个入口(去 fork)**:时间跳跃禁词 / 套路比喻 / 星期算错此前**各自在
  chat_pipeline 的 async 与 sync 两路手写一遍**(时间跳跃甚至第 3 处在 `_run_post_gm_parallel`),
  = 6+ 处孤立接线(1.35.0 的星期验错又新增一处 = 屎山)。统一到
  `timeline_narrative_guard.run_narrative_guards(response, player_message, state)` 单一入口:检测 →
  写 audit → 返回待 yield 的 SSE 事件;async/sync 两路各调**一次**,`_run_post_gm_parallel` 只留黑天鹅 +
  extractor。**行为零变化**(同样的 timeline_guard/cliche_notice/weekday_notice 事件)。以后加新的确定性
  叙事纠错只改这一个函数。

## [1.35.0] - 2026-07-02 (@ c60225ce9)

### Added
- **确定性星期验错**(客户 abci 反馈:LLM 算不对星期,「今天周日→明天却写成周六」,重生成六七次才蒙对):
  `timeline_narrative_guard.detect_weekday_violations` —— 剧情里有**确切**「今天=周X」基准 + 相对日
  (明天/后天/大后天/昨天/…)配了星期时,用算法算出正确星期、**查出 LLM 算错的**,以 `weekday_notice`
  事件 surface(前端 toast 明示「明天应为周一,AI 写成了周六」+ 可重新生成)并记 audit。**默认休眠**:
  没有确切「今天=周X」基准就完全不触发 → 玄幻/修仙/无日历剧本零副作用(剧本演绎不硬塞日历)。**只查不
  改**:不注入日历、不改写正文、不搞状态机。认周/星期/礼拜 + 一~日/天 + 阿拉伯数字;合并玩家本回合输入
  + GM 输出做自洽检查(玩家立「今天周日」、GM 写错即查出)。8 项测试含客户原例 + 无日历剧本零误伤。

## [1.34.5] - 2026-07-02 (@ db95de8e3)

### Fixed
- **可以把没有 embedding 接口的 provider(DeepSeek/Anthropic)选成嵌入器 → 每批 404、重试 5 次
  (~2.5 分钟)才放弃、导入的小说 RAG 坏掉**(生产日志:user 188 `embed.api_id=deepseek` +
  `text-embedding-004`)。各层校验都漏:picker 只按**模型名** heuristic 判 embedding(`text-embedding-004`
  名字命中)、preflight 只查「有没有该 provider 凭据」(deepseek 聊天凭据存在 → 判已配置)、resolution
  盲目配对。补三层权威闸:①后端 `embedding.provider_lacks_embedding(api_id, base_url)`(按 provider +
  base_url host 双判 deepseek/anthropic/moonshot),嵌入循环**绑定前快速失败**并给明确指引(改选支持
  embedding 的 provider),不再 404-loop;②`/api/me/embedder-status` 把无 embedding 的选择判为**未配置** +
  返回 `embed_provider_hint`;③前端 `AgentModelPicker`(`capabilityFilter='embedding'`)从嵌入器列表
  **排除**这些 chat-only provider,即便配了聊天凭据也不显示。

## [1.34.4] - 2026-07-02 (@ 4de3ceb5e)

### Fixed
- **导入小说时世界书 LLM 抽取阶段崩溃、条目近乎不入库**(生产日志排查发现,script 248/user 188):
  `import_pipeline._stage_worldbook` 用 `count += db.rowcount`,但 psycopg3 的 `rowcount` 在
  `execute()` 返回的 **cursor** 上、不在 **Connection** 上 → 插入第一条后即 `AttributeError:
  'Connection' object has no attribute 'rowcount'` → 整个 LLM 抽取阶段被 except 吞成
  `[worldbook] LLM extract failed`、后续条目全不入库。改为从 `db.execute(...)` 返回的 cursor 取
  `rowcount`。含回归测试 + psycopg3 契约测试。

## [1.34.3] - 2026-07-02 (@ c47db1690)

### Fixed
- **回合卡在「生成中」→ 几十秒后「生成失败,连接超时」,三四次才成一次;改写还要等 2-3 分钟**(行者无疆
  严重反馈):acceptance A/B 的**改写候选(第二次完整 GM 调用)被放在回合关键路径同步跑**(v1.32.9 批次4
  用 `to_thread` 塞回主路径)—— 正文流完后还要等一次完整 GM 生成(provider 慢/503/超时可达 2-3 分钟),
  期间 SSE 无事件 → 前端不活跃超时 → 整回合判失败(即便首稿早已生成)。根修:改写候选**下线关键路径** ——
  async 生产路径把改写 fire-and-forget 丢**后台任务**(`asyncio.create_task`,GM 调用走 `to_thread` 不塞事件
  循环),回合立刻收尾发 `done`;候选生成后经 `state_event_bus.emit('acceptance_alt')` 跨 worker 推给前端
  (长连 `/state_events` → 前端收到再挂双栏 A/B)。verify+audit+节流仍内联(默认 rule 模式=确定性极快)。
  这也恢复了 W1 容量意图(回合 slot 不再被第二次 LLM 占住)。sync/测试路径保留内联(便于确定性测试)。
- **`/api/message/edit` 对 kb_native 存档编辑后刷新回退**(与 acceptance 选择同源):手动编辑历史消息只改
  messages 表 + `state.save()` blob,没改**活跃 commit 快照**(kb_native materialize 权威源)→ 刷新/换 worker
  回退。改为复用 `_amend_history_message` 写穿所有存储 + bump snapshot_hash(跨 worker 失效);支持任意角色编辑。

## [1.34.2] - 2026-07-02 (@ 62510f0c8)

### Fixed
- **恢复历史存档里已被误归档移出的能力/资源**(承接 1.34.1 的「彻底修复」):1.34.1 只止损(往后不再归档
  abilities/resources),但老玩家**此前已丢**的条目不会自己回来。新增自愈 `_restore_persistent_buckets`
  (MemoryProvider 每回合幂等调):把 `memory.items` 里 `legacy_bucket ∈ {abilities,resources}` 且被(旧)
  auto-archive 标 archived 的条目救回对应 bucket + 取消 archived。安全:auto-archive 是唯一置 archived 的路径;
  玩家显式删除的条目 `remove_memory` 已硬删出 items(不会被复活);superseded 跳过。含回归测试(恢复 +
  不复活 superseded/已删)。

## [1.34.1] - 2026-07-02 (@ ae4521119)

### Fixed
- **手动添加的能力/资源随剧情推进消失、条目减少**(行者无疆反馈):`_maybe_auto_archive` 每
  `summary_window`(默认 10)轮把 turn 早于 `current_turn - auto_archive_after_turns`(默认 50)的记忆条目标
  archived 并**从 legacy bucket 移除**。v1.27.4 已豁免 notes/pinned,但 **abilities/resources 没豁免** →
  角色能力/物品/货币是【角色卡式持久状态】(不该因回合数增长而静默消失,手动加的尤其荒谬),几十回合后被
  自动归档移出 bucket → 面板条目减少、GM 也看不到。修:abilities/resources 与 notes/pinned 一同豁免自动
  归档(bucket 剥离也只剩 facts);**只有 facts(叙事流水)才自动归档**。含复现测试(turn 60 归档窗口)+
  真实 `GameState.add_memory` 写入路径回归。

## [1.34.0] - 2026-07-02 (@ 925f8bc38)

acceptance A/B 二次迭代:选择持久化根修 + 用户级开关(行者无疆二次反馈 + 承诺)。

### Fixed
- **选了改写(第二套)、过一会/刷新/切页后又变回首稿(第一套)**(行者无疆二次反馈):`/api/acceptance/choice`
  的换稿旧实现只改 `messages` 表 + `state.save()` blob,但 **kb_native 存档(现所有新档)刷新时 materialize 从
  【活跃 branch_commit 快照】的 history 读**(`save_kb.materialize`;messages 表只是空 history 时的兜底)——没改
  那里 → 换 worker / 缓存失效重 materialize 就回退首稿。根修:`_amend_history_message` 自包含写穿所有刷新会读到的
  存储 —— 活跃 commit 快照(权威)+ game_saves/runtime_checkouts 工作树快照 + **bump runtime snapshot_hash**
  (跨 worker 缓存失效,其它 worker 下次请求 hash_drift 重 materialize;同固定记忆 out-of-turn 编辑范式)+ messages 表,
  全程内容匹配替换(避免各存储 index 基准不一致)。

### Added
- **AI 改写建议开关**(游戏内设置,用户级 `user_preferences['acceptance_ab.enabled']`,默认开):玩家可手动关掉
  acceptance A/B 改写候选(关掉则始终只用首稿,不再弹选择)。后端 `_acceptance_gate` 在生成候选前读该开关。

## [1.33.0] - 2026-07-02 (@ 2474a5fc9)

acceptance 硬闸从「静默重写替换首稿」改造成「节流 + 双栏 A/B 玩家裁决 + 数据采集」。

### Fixed
- **正文流完后 5-10 秒被整段换成另一套文字**(行者无疆反馈,v1.32.9 批次4 起在生产暴露):acceptance 验收自检发现首稿有未覆盖的验收点时,会同步重跑一次 GM 重写整段,并**直接替换**首稿(response/state 都换成第二稿)。流式路径下前端在 `done` 时用服务端最终 history 覆盖流式气泡 → 客户看到「意思不变、文字全变」。根因:流式是对用户的承诺,已读正文不该被事后重写替换。现改为**首稿(玩家流式读到的)永远是权威版**,gate 不再 apply 第二稿 ops、不替换 canonical → 跳变在游戏台/酒馆/移动端一并消失。

### Added
- **acceptance 改写 A/B 玩家裁决**:首稿有 unmet 且**节流放行(每存档最多每 5 回合一次)**时,额外生成一份「改写候选」,以新 SSE 事件 `acceptance_alt` 下发前端,在气泡下方**双栏并排**展示「当前版本 / 改写版本」,玩家自己选。选改写才把该轮消息换成**服务端存的**改写稿(前端不回传正文,防注入)。
- **数据采集层**(migration v91 `acceptance_ab_log`):每次提供候选落一行(unmet + 首稿 + 改写 + 玩家选择),用于统计玩家偏好、迭代 acceptance 算法。
- 新端点 `POST /api/acceptance/choice`(记录选择 + 选改写时按 `message_index` 换消息,IDOR 归属校验,复用 `/api/message/edit` 落库路径)。

## [1.32.15] - 2026-07-01

编辑器 AI 写作副驾审计修复 · 批次B。

### Fixed
- **编辑器写模式不安全默认**(P2):`_resolve_editor_write_mode` 直接信 `_normalize_permission_mode`,而后者对**任何未识别串**兜底成 `full_access` → DB 里一个坏值(迁移残留/拼写/废弃键)就静默跳过所有写确认。改为只认**显式** `full_access`,未识别/default 一律回最稳 `review`(与空串路径对称)。
- **确认流 dual-store 不一致**(P2):`_resolve_pending` 只从 Redis 拉对话,Redis TTL(6h)过期/不可达时报「对话不存在」→ 玩家隔几小时回来点确认就失败。补 PG 回落(与 `_get_or_create` 的 Redis||PG 对齐)。

## [1.32.14] - 2026-07-01

md 编辑器 AI 写作副驾(`console_assistant`)系统性审计后修复 · 批次A(2 个 P1)。

### Fixed
- **编辑器对话重启即丢**(P1):`persist_conversation` 把「Redis 未配置/不可达」的 `return` 放在 `_persist_conv_pg` **之前** → Redis 关掉时(本地/桌面无 Redis)PG 永不写,与注释「再落 PG 永久保留」正好相反,worker 重启后对话历史全丢且无从恢复。改为:Redis 写尽力而为,PG **无条件**写(与 Redis 可用性解耦)。
- **「激活存档 → 进入游戏」永远静默失效**(P1):`navigate_to_setting` 的安全白名单 `_NAV_TARGETS_WHITELIST` 漏了 `game_console` —— 而 tool 枚举、system prompt、前端导航 MAP 早就用它;非白名单 target 被静默置空、不发导航事件 → 用户激活存档后卡在 Platform 页不动。补进白名单(与 GM 管线同一类 fork:声明了却没接进门控)。

## [1.32.13] - 2026-07-01

### Fixed
- **世界书双注入 —— 保留两种激活模型、只删重复**:两条世界书注入路径**不是冗余而是互补** —— A(`retrieve_context`:`priority>=80` 无条件常驻 + query 命中)与 B(`NovelWorldbookProvider`:最近对话关键词激活 + reveal 剧透门控)覆盖不同情况,删任一条都会丢一种覆盖(高优设定的关键词这几轮没提到时删 A 就消失)= 回归。真正的浪费只在**两条都命中的重叠**。修:A 把已注入条目的**唯一 id**(`db_{id}`,非 title —— worldbook 常同名/空 title)挂到 `state` 瞬态属性,B 跳过这些 id;属性缺失时不过滤 = 原行为(无回归)。两模型都保留,只删重复注入。

## [1.32.12] - 2026-07-01

流水线去 fork · 批次7(收尾全清)。

### Fixed
- **「当前目标·主线」不更新**(群反馈,行者无疆):`memory.main_quest`(面板顶部醒目)与 `memory.current_objective`(底部灰字)是两个字段,GM 每回合发 `【当前目标】` 只写后者,`main_quest` 只有发 `【主线】` 才更新 → 固化在开局值。改为**从当前 phase 派生** `main_quest`(`retrieve_context` 调 `_resolve_active_phase_range`,锚点系统已知当前阶段)。非破坏:仅当 `main_quest` 为空、或仍是上次自动派生值(=没被玩家 `set_main_quest` / GM `【主线】` 手改)时刷新,保护手写主线。
- **`_default_judge`(`recorder_unified` 关时走)不产 `progress_motion`**:该路径 pace fallback 也失效(与批次1 修的史官三合一路径同类)。补上 `progress_motion` 产出与解析。
- **`candidate_actions` 措辞矛盾**:`master.py`「不得原创不在候选里的动作」↔ `rules_text`「不强制」→ 统一为「优先参考、不强制」。
- **`confidence < 0.5` 提示词与实际不符**:声称主 GM 本轮不出场,但管线早已移除该短路 → 对齐真实行为(主 GM 仍做最合理解读推进)。
- **`_worldline_layer` 死代码删除 + 权限模式行为说明移植进 `WorldlineProvider`**:provider 化迁移遗漏了这段 → GM 之前只看到「只读模式」标签、不知其写入语义(read_only 下写入全进 pending 应改用询问)。

## [1.32.11] - 2026-07-01

流水线去 fork · 批次6(真机 e2e 复查 + 审计遗漏补修)。

### Added
- **async acceptance retry 真机 e2e**(`test_acceptance_retry_fires_e2e.py`):真实 chat 端点 + async 生产路径 + 真实 rule 验收器,只 stub curator/GM(第一稿不满足→触发 retry→第二稿满足)。验证回合 200 无 error、GM 被调 2 次(retry 真 fire)、`acceptance_retry` 事件、最终落库第二稿。补上批次4 之前缺的真回合验证。

### Fixed
- **reveal_clause_v2 v2 前沿门控漏传 progress_chapter**(P1,休眠于 `RPG_TKB_FRONTIER` off):3 处 `_rc_v2` 调用(主/parent/shadow)漏传 → `save_visible_anchors` 空(新档)时带 `reveal_anchor_key` 的实体全被过滤、层级树空。补齐。
- **harness 时间跳跃路径漏 recall/time-value 双门**(P2):harness(所有 BYOK 主路径)缺了 `llm_curator` 分支已有的 `is_recall_framing` + `looks_like_time_value` 两道门 → 玩家「回想起…」触发假时间跳跃(v1.26.4 只修了另一分支)。对齐。

## [1.32.10] - 2026-07-01

流水线去 fork · 批次5(收尾)。

### Fixed
- **`/set story_intent=X` 对 GM 导演层无效**:之前写到没人读的顶层 `data["story_intent"]`,而 WorldlineProvider 读的是 `player_private.story_intent`(建档 dual-write 字段)→ 游戏中改 story_intent 白改。`apply_state_write_typed` 统一路由 `story_intent → player_private.story_intent`(与建档+读取同源)。
- **`world.weather` 被权限白名单漏掉**:recorder/extractor 提示词声明天气可写,但 `default`/`auto_review` 白名单不含它 → 每回合静默入 pending 审批。天气是低风险叙事态,纳入白名单(`read_only` 仍拦)。

## [1.32.9] - 2026-07-01

流水线去 fork · 批次4(async acceptance retry 真正生效)。

### Fixed
- **async(生产默认)下 acceptance retry 名存实亡**:`_POSTPROC_MODE=async` 时 GM 流完即 early-return,整个 acceptance verify+retry 块被跳过 → 生产上只剩 worker 事后审计、不重写(原为 W1 容量优化的取舍)。fork 收编:把 verify+retry+重 apply 抽成单一同步闭包 `_acceptance_gate`,sync/async 同源(消除「两路各写一套、只在 sync 实现」)。async 两处 early-return 前用 `asyncio.to_thread` 调它 —— 阻塞的 retry GM 调用跑线程里不塞事件循环,既恢复生产 retry、又不牺牲 async 容量。全程 try/except 兜底(闭包恒返 3-tuple,任何失败退回原稿);逃生开关 `RPG_ACCEPTANCE_RETRY=0` 仍在。

## [1.32.8] - 2026-07-01

流水线去 fork · 批次3(确定性 P2 簇)。

### Fixed
- **acceptance retry 第二稿工具集与首稿不一致**:`narrator_slim` 档首稿用精简 `_gm_tools`,retry 却写死 `unified_tools` → slim 档 retry 触发本该剔除的重型写工具、与史官竞争写入。统一为 `_gm_tools`。
- **novel RAG 原文的 `【】` 状态标签未中和**:provider 化后漏了旧 fallback 路径的 `_neutralize_state_write_tags` → 485 万字网文里的 `【签到奖励】` 等括号被 GM 复述后被 `apply_structured_updates` 误当状态写入执行。补中和(anchor 段同理)。
- **酒馆/模组层不在 `MAX_LAYER_CHARS`**:`tavern_character/tavern_card_system/tavern_persona/module_scene/module_encounter` 走默认 1800 → 角色卡/persona 被截断。补 5 个层预算。
- **RulesProvider 动态层错进 A 级缓存**:layer id 与静态 `rules`(A 级)撞 → 含每回合变的 HP/骰子日志被标 ephemeral 缓存但每回合 miss(零效率)。改独立 id `rules_state` + 显式 `cache_tier="C"`。
- **进度回退端点整列读-改-写竞态**:workers=2 下与并发 `advance_progress` 竞态吞更新、整列覆盖抹掉其它 worldline 键(进程内 RLock 跨 worker 无效)。改原子 `jsonb_set`(只动 progress_chapter)。

## [1.32.7] - 2026-07-01

流水线去 fork · 批次2(mode 打通)。

### Fixed
- **发散局仍被强推「修炼/主线」、日常/亲密场景也被拽回主线**(群反馈 行者无疆;审计 A 根因):引导强度开关 `steering_strength`(rail 贴原著 / guided / free 自由)此前只接到 `steering.py` 一处,curator 造 acceptance、steering 末节点、retrieval 收束段都无视它,一律按 rail 强推 canon → free 局玩家被 railroad。mode 贯穿整条目标管线(fork 收编):① curator(`_curator_task_prompt` 现读 `steering_strength`,与 retrieval 同源)按档生成 acceptance —— free **严禁**凭空造「推进主线/修炼进度」的验收点、主线仅作背景,guided 温和且玩家跑题以其意图为先,rail 维持收束;② `steering.py` 末节点补 free 分支(不注软目标);③ `retrieval.py` 世界线收束段加 `steering_strength != "free"` gate。admin 账户真机 e2e(剧本6《我蕾穆丽娜不爱你》):rail 注入收束段 / free 已 gate 掉。

## [1.32.6] - 2026-07-01

GM 每回合流水线系统性审计(43 原始发现 → 30 对抗验证确认)后的分批去 fork 收尾 · 批次1(全确定性根修)。

### Fixed
- **发散局进度冻死在 Anthropic/Vertex(主力 provider)**(审计 P1):`progress_motion` 只声明在 recorder system prompt 文本里,没进 `_build_tool_schema` 的 anchors 块 → 原生 tool-use / Vertex function-call 下 LLM 不吐它 → `_safe_progress_motion(None)` → `do_pace=False` → pace fallback 永不触发(此前只对 OpenAI-compat 有效)。补进 tool-schema(收 provider fork)+ 加 parity 守卫测试(tool-schema 与 system prompt 必须同源声明该字段)。
- **acceptance 跳过元信息污染活事实库(污染回路 B)**:`master.py` 曾指示 GM 把「acceptance 'X' 跳过因为 Y」写进 `memory.facts`,被 MemoryProvider/short_summary 每回合回读 → 自强化污染剧情记忆。双保险:`apply_ops` 落库层确定性拦截该类字符串 + `master.py` 改指示直接跳过、不写任何状态字段(审计的事)。
- **acceptance 存 8 渲 6 → 必然假 unmet retry**:curator 存 `acceptance[:8]` 但 GM prompt 只渲染 `[:6]`,第 7/8 条 GM 从没见过却被 verifier 检查 → 必然判 unmet + 白烧一次 GM 调用。存储上限收敛到 6,GM 所见 == verifier 所查。

## [1.32.5] - 2026-06-30

### Fixed
- **选「出生点」开局仍从序章 / 贴原著正文+对话消失**(群反馈 #62/#63/#66/#67):入场选出生点(从原著第 N 章开局)时,只把章节范围写进 `world.timeline.anchor_chapter_range`,却没灌进进度信号 `worldline.progress_chapter`。后果:`retrieve_context._progress_chapter` 默认 1(reveal 闸锁序章、第 2 章以后角色被藏)、`get_progress_window` 退回 `[1,30]` 兜底 → 待发生锚点窗口 / NPC 抽取 / ongoing 回合贴原著正文全按序章走。修(两处确定性缝,出生点=玩家显式选择的确定性起始章):① `_build_initial_snapshot` 出生点同时写 `worldline.progress_chapter=chapter_min`(`_PRESERVE_SETTINGS_SQL` 已含该键 → 跨回合 sticky;`advance_progress` 仍可 max 前推);② `get_progress_window` 无 occurred 锚点时读 `worldline.progress_chapter` 作下限(优先于易错的 `world.time` 标签匹配)。真库回归 `test_birthpoint_progress.py`(4 passed)。复核确认 #73/#77/#82-84 多为本根因下游、或既有 `_apply_pace_fallback` 已兜底,无需再动 GM 路径。

### Added (internal tooling, 不影响线上运行)
- **bench 叙事质量度量闭环 — LLM 裁判层**:在确定性 bench 之上补 pairwise 4 维裁判(faithfulness/coherence/identity/spoiler_control)+ anti-position-bias 校准 + 合并报告(`rpg/bench/judge*.py`、`run_judge.py`)。离线逻辑测试 `test_bench_judge.py`(10 passed);真模型端到端标定待 evomap key。
- 自包含存档 export→import 无损往返回归 `test_save_import_roundtrip.py`(锁住已修的 #78;#64/#71/#78 修复早已在线)。

## [1.32.4] - 2026-06-30

### Fixed
- **回归:删除 GM 回复(v1.30.1 引入)删不掉它**(自查 8 个修复时发现):v1.30.1 把 rollback 统一成 `target_turn = msg_index//2` 对齐 fork,但对**偶数 index(GM 回复)**而言,N//2 正是该 GM 回复所在回合,回退到该 round commit 会把这条回复一起【保留】→ 用户点「删除此 GM 回复及以后」却发现它还在(只删掉了后续回合)。修:奇偶分开——奇(玩家输入)保持 N//2(原 off-by-one 修复不变);偶(GM 回复)再退一格 `max(0, N//2-1)`,把该回合连同这条 GM 回复一起删。删除弹窗去掉不再成立的「玩家输入保留」字样。守卫测试加偶数/奇数用例。

## [1.32.3] - 2026-06-30

### Fixed
- **后期角色出现在 GM 每轮思考里(反馈 #84.1)**:`novel.py` 注入待发生锚点用 `limit=20` + 50 章窗口 → 把远未来锚点(尚未登场的后期角色,如无限流「楚轩」)成批灌进 GM 上下文 → 思考被未来角色污染、徒增 token(真库 save 268:20 条全是原著郑吒剧情线、楚轩在第 8 条)。按章近优排序后**只取最近 6 拍**(`limit=20→6`):楚轩等远锚点不再进 GM视野;不影响进度计算 / pending NPC 强制注入 → 无 stall 风险。真库验证 limit=6 后楚轩已被砍。

## [1.32.2] - 2026-06-30

### Fixed
- **GM 把穿越者玩家与原著男主搞混(反馈 #87)**:无限流/同人局里 GM 偶把穿越者玩家当成原著主角(把主角的身份/剧情位置/能力/际遇套到玩家身上)。诊断:玩家身份本身确定性正确(player.name 保护 + 代入别名去重均就位,真库 save 268 玩家=赵时·肉穿、别名空、未与郑吒去重混淆),混淆在【自由生成层】——出身机制提示只说「你是外来者」、从不显式把穿越者与原著主角分离。修(提示层缓解,无确定性信号可强制生成层身份独立):穿越者出身(soul/body/dual,native 豁免)的动态上下文追加『身份独立·铁律』——玩家是独立新角色、原著主角是并存的独立 NPC、GM 绝不把两者混为一谈或让玩家顶替主角剧情位置/自动获得其际遇。

## [1.32.1] - 2026-06-30

### Fixed
- **配好 API 却查询不到模型(反馈 #91)**:中转站 base_url 不带版本段(如 `https://relay.com`)时,OpenAI SDK 打 `{base}/models` 而非 `/v1/models` → 403/404 → 0 模型(真库复现:evomap `/v1/models`=200、`/models`=403)。`model_probe._list_openai_compat_models` 列模型失败且 base_url 无 `/vN` 版本段时,自动补 `/v1` 重试一次(仅失败时、仅缺版本段,不掩盖真错、不动 `/v1beta/openai` 等)。错误文案也提示「base_url 可能缺 /v1」。
- **角色卡 >5MB 无法上传(反馈 #92)**:前端导入硬限 5MB,但后端实际可收 8–16MB → 5–10MB 的卡被前端挡下。`cards.jsx` 与 `MobileCards.jsx` 上限 5MB→**10MB**(对齐后端 PNG 导入上限)。

## [1.32.0] - 2026-06-29

### Added
- **状态面板「能力 / 技能」可增删区**(群反馈,行者无疆「status 面板参数不可动 / 修来的能力只能写笔记?」):`memory.abilities` 桶其实 GM 检测到「掌握 / 习得」就会自动写(真库 save 268 已有 2 条修炼能力),但前端状态面板从不显示 → 用户以为没地方记。状态面板(NovelStatusProfile)新增「能力 / 技能」区:列出 `state.memory.abilities` + 右上「+」手动添加 + 逐条删除(复用既有 `/api/memory/add|remove` 的 abilities 桶,与固定记忆增删同款),中英 i18n。修来的能力终于有结构化的家。

## [1.31.3] - 2026-06-29

### Fixed
- **事实库大量重复条目(群反馈,行者无疆「这一条就有9条」)**:kb_native 档的 `memory.facts`/`world.known_events` 用 index-keyed logical_key(`fact:{i}`/`kevt:{i}`)存进 kb_events。桶收缩 / 重排后,高 index 的旧 `fact:{i}` 行**不退役**,同一文本残留在多个 logical_key 上,`_newest_visible` 各取一行 → `materialize` 重复读出(真库 save 268 实测 memory.facts 149 条仅 41 唯一,某条 ×15),且自我累积(materialize 重复→import 写更多 index)。修两层:① `save_kb.materialize` 按 summary 去重(保序)→ 所有存档**下次加载即干净**(显示 + GM 上下文);② `save_kb.import_state` 写前桶去重 + 写后按当前长度**退役高 index 孤儿** → 根治累积,存档下回合自愈、不再增长。真库 save 268 materialize 验证 + 回归测试 test_kb_facts_dedup。

## [1.31.2] - 2026-06-29

### Fixed
- **游戏内切换模型不生效 / 永远跑旧模型(群反馈,白玖,反复出现)**:真根因=`persist_session_model` 的 SELECT `join user_runtime ur on ur.checkout_id=rc.id` 引用了 **user_runtime 不存在的列 checkout_id** → 每次抛 UndefinedColumn 被外层 except 静默吞掉 → session_model 从不落 runtime_checkouts → 跨 worker 模型漂移检测(读 DB session_model)永远拿不到新值,逻辑对但数据源被静默掐断。workers=4 下切换只在处理该请求的 worker 内存生效,绝大多数 GM 请求落到没切过的 worker → 旧模型(日志 [GM] zhipu …)。修:persist 改按 (user_id,save_id) 取 runtime_checkouts(与 read_runtime/_attach_db_state 同一行,该组合唯一)。第二层(kb_native):materialize 从 kb_worldline_vars 拿到的是上回合旧 session_model 会 clobber 刚切的值 → `_kb_backed_state` 保留 working-tree (runtime_checkouts)的 session_model。真库往返复现 + 回归测试。

## [1.31.1] - 2026-06-29

### Fixed
- **txt 导出残留代码围栏**:`export_transcript_txt` 的清洗只过开场三件套(按合法 op 模式匹配),漏掉**畸形 / 未闭合**的 ```json 围栏(如 GM 偶发吐出 ```json\n[, 截断块)。真库 save 268 导出实测仍含 1 处。修:清洗末尾再整块去代码围栏——先去成对 ```...```,再去单条未闭合 ``` 到本条消息结尾。对「当小说」更干净。

## [1.31.0] - 2026-06-29

### Added
- **导出对话为人类可读 .txt(当小说分享)**(群反馈,白玖):游戏台顶栏与酒馆头部各加一个「导出 TXT」按钮(book 图标),把整段对话整理成不含 ops/代码的可读文本下载。后端 `GET /api/saves/{id}/export/txt`(游戏 / 酒馆通用,二者皆 game_saves 行)读活跃 commit 的 blob history(分支隔离,与所见一致),逐条剥掉 ops JSON / 工具脚手架(复用开场清洗三件套)+ 去玩家输入的 slash 指令前缀,玩家发言(标玩家名)与 GM 正文交替成文,UTF-8 attachment 下载。

## [1.30.1] - 2026-06-29

### Fixed
- **「删除此消息及以后」多回退一个回合(群反馈,行者无疆/晓卡/星之游「修了一个出来两个」)**:`rollback_to_message`(delete 路径)用 `message_row_by_index` 读 flat `messages` 表定位回退点,但该表含开场空 user 行、且非分支隔离 → 与前端 blob history index 错位 ≥1 位,导致软回滚的目标 commit 系统性偏早一回合(要手动去分支树切回来)。fork 路径(`resolve_commit_id_by_message`)早已改用 `msg_index//2` + 活跃血缘,delete 路径漏同步;v1.28.1 分支隔离 materialize 让 messages 表与 blob 进一步背离、放大错位。修:delete 与 fork 同口径——`target_turn = msg_index//2`,内联活跃 commit 血缘递归定位(不调用 fork 版以免在 advisory 锁内嵌套开连接致池死锁),不再用 `message_row_by_index`。真库 save 268 跨 index 1..7 验证NEW 恒为 OLD+1 且落在真实 turn;加源码不变量回归守卫。

## [1.30.0] - 2026-06-29

### Added
- **角色卡侧栏「本轮调用」标记**(群反馈):侧栏现在标出哪些角色卡在本回合被注入了 GM 上下文。数据源是后端既有的 `last_context` 的 `npc_cards` 层(`_active_character_cards` 按当前输入 / 在场 / pending anchor 命中,`core.py` 每层保留 `items`),纯前端读取——`当前在场` 与 `已固定角色卡` 中名字 / 别名命中的卡显示一枚 accent 小药丸标记。不另起页面、零后端改动。空 / 首屏(尚无回合)不显示。

## [1.29.0] - 2026-06-28

### Added
- **设置「保留未响应的对话(可重试)」开关(默认关)**(群反馈,行者无疆):此前一轮无回复 / 生成失败 / 被中断时,前端会自动撤回本轮玩家发言(`restoreFailedDraft` 删掉乐观气泡 + 还原草稿),用户反馈「会回退一个对话」。新增纯前端开关(localStorage `gc.keepFailedTurn`,游戏内设置面板),开启后失败轮**保留**在对话里 + 由错误条的「重试」按钮重发,而非自动撤回。默认关 → 现有行为不变,纯加法。失败轮仍不落库,推进/重试时被后端真值自然替换。

## [1.28.5] - 2026-06-28 (@ 6ea0ad03e)

### Fixed
- **固定记忆删改「可以删但一推进剧情就回归原样」(行者无疆,第四层根因,真库复现+回归测试)**:根因在 `persist_runtime_state` 的指针发散守卫。回合后 `game_saves.active_commit_id` 领先、`user_runtime` 由 `update_active_node` 异步同步**滞后**,旧逻辑一看 `db_active != commit_id` 就**无条件** `state_data = db_snapshot`,把刚做的 out-of-turn 编辑(固定记忆/笔记增删)连同 incoming state 一起丢掉 → 删除在缓存里生效(面板显示删了),但持久层是旧值,**一推进剧情(回合加载真值)就回退**。修:指针滞后 ≠ state 过时(异步窗口里 loaded state 往往是最新的);仅当 incoming 质量确实更低(基于更早回合、history 更短)才采用 db_snapshot,否则保留 incoming。真库复现发散场景 + 验证(发散删除保留 / 真过时仍防丢回合)。这是该反复 bug 的第四层(前三层:dual-write 同步 / 豁免归档 / 跨 worker 缓存 hash 漂移)。

## [1.28.4] - 2026-06-28 (@ 9d9412e43)

### Added
- **RP harness 基准框架扩展(`rpg/bench/`,离线工具零运行时):** replay A/B 引擎——真实存档上下文喂【线上记录基线】vs【候选 OpenAI 兼容模型/提示词】,同 metrics 并排打分;确定性核心指标 `unknown_speaker`+`prior_echo`;写小说续写基准——真实章节前半→续写→比真实后半(style_overlap/canon_drift/prefix_copy/gen_repeat/length_ratio)。`bench/README.md` 三模式用法。本地真实数据(deepseek-v4-flash)验证通过。

## [1.28.3] - 2026-06-28 (@ e09cbbee0)

### Fixed
- **固定记忆/笔记 删改后「已删的又回来」(反复出现,群反馈 行者无疆)**:根因=跨 worker 缓存不感知 out-of-turn 编辑。`persist_runtime_state` 写固定记忆 bump `row_version` + runtime `snapshot_hash` 但**不 bump commit**(设计上 autosave 不建新回合),而 `_ensure_loaded` 的缓存一致性自检只比 save/commit/model → `workers=2` 下另一 worker 缓存仍是旧 state,"删 A→加 C"落在旧 [A,B] 上 → A 复活。修:缓存自检增加 **snapshot_hash 漂移**(DB 真值,`read_runtime` 已带、无额外查询),侧改后另一 worker 缓存即失效重载(与既有 model_drift 同款)。另:`edit_memory` 新方法让"改"也同步结构化 `memory.items`(原 `/api/memory/update` 只改 legacy bucket、GM 上下文读 items 看到旧文本)。回归测试覆盖 add/remove/edit 双写一致。

## [1.28.2] - 2026-06-28 (@ a5ac0c427)

### Fixed
- **开场把结构化 ops JSON 漏给玩家**:开场流程(`routes/game.py`)只抽走尾部 markdown 选项,**没有**走 chat 路径落库前那套清洗 → GM 的 ```json `[{"op":...}]` 围栏(及工具元叙述 / 泄漏脚手架)被原样存进历史 blob + messages,显示给玩家(基准测出多档开场命中,save 8 开场 841 字里 454 字是 JSON)。修:开场复用 chat 同一套 `strip_json_state_ops` → `strip_meta_tool_preamble` → `strip_leaked_scaffold`(结构化解析仍用含 ops 原文,只清洗"给玩家看 + 落历史"的版本)。真实泄漏开场上验证 ops 围栏被剥净。

### Added
- **RPG Roleplay 专属 harness 基准框架(`rpg/bench/`)**:可插拔指标注册表(`@metric`)+ 真实存档回合 case 提取(从 commit blob,分支正确)+ scorecard 聚合(坏指标命中率 / 观测率 / 连续分位 / worst offenders,纯 JSON 可跨 run 比较)。首版确定性指标:退化复读 / 语言降级 / 出戏自曝 / 协议泄漏 / 长度健康 / canon 接地。用真实用户交互数据评估当前 harness 基线、回归对照,后续接 replay(候选 harness 现生成再打分)做 A/B。**离线工具,零运行时影响。** 首跑即测出上面的开场 ops 泄漏 bug。

## [1.28.1] - 2026-06-28 (@ 7fa4ca6d4)

### Fixed
- **新建分支没删除老分支 / 新建存档顶部出现空白玩家输入（反复出现，深度审计）**:根因在 `kb/save_kb.py::materialize`。新存档自创建即 `kb_native=true`（`_seed_kb_at_creation`「封死新存档入口」），其会话历史走 `materialize()` 重建——而它从 `messages where save_id` 读历史。`messages` 表按 `(save_id, turn)` 存、**无分支维度**,同一存档的所有分支消息共享 `save_id` → ① 切/建分支后老分支对话仍被读出(「老分支没删」);② 开场把空 `player_input` 也落了 `messages` → 顶部一条空白玩家气泡。修:`materialize` 改从**本 commit 的 `state_snapshot` blob** 读历史(按 commit DAG 逐分支隔离、开场只含 assistant,与非 kb_native 路径同一份),blob 缺失才回退 `messages` 并滤空行;同时 `_db_insert_turn_messages` 开场不再写空 user 行(messages 与 blob 下标对齐,消息编辑端点不错位)。真库 e2e 复现跨分支污染 + 空开场并验证修复。
- **剧本编辑器编辑时间线锚点保存失败「无可更新字段」**:锚点摘要 DB 列名 / GET / timeline / md-editor 往返全用 `sample_summary`,而 `PUT /api/scripts/{id}/anchors/{id}` 旧逻辑只认 API 名 `summary` → 编辑器回发的 `sample_summary` 被忽略,只改摘要时报错。修:`_anchor_update_sets` 两个名都收(优先 `summary`,回退 `sample_summary`)。

## [1.0.5] - 2026-06-19

### Fixed
- **切换模型不生效(严重)**:`_gm_by_user` 为 per-worker 内存缓存,`/api/models/select` 仅 evict 处理该请求的 worker;`workers=2` 下另一 worker 仍跑旧模型(且 `session_model` 变更不 bump commit,save/commit drift 抓不到)→ 用户「无论切什么都跑某固定模型、烧错 provider 的 token」。修:`read_runtime` 顺带取 DB 真值 `session_model`(零额外查询),`_ensure_loaded` 检测跨 worker 模型漂移并失效 state+GM 重建。
- **上下文用量「对话历史」越聊越少**:native-tools 路径(anthropic/vertex/openai-compat)不写 `last_context` token 估算 →「对话历史」只显示当前输入长度。**纯显示问题,模型实际收到完整历史**;已对齐文本路径补算。
- **酒馆「正在思考…」浮条**:改为「思考过程」折叠条同款克制样式(标签 + 右侧转圈),去掉突兀的大圆角浮条。

## [1.0.4] - 2026-06-19

### Fixed
- 中转站 base_url 自愈:用户把文档里的完整「接口地址」`https://host/v1/chat/completions` 整段填进 base_url,导致 SDK 再拼 `/chat/completions`、`/models` 双双 404 →「不可访问 / 0 模型」(如 EvoMap)。现在 `set_credential` 写时 + `get_credential` 读时都自动剥掉 `/chat/completions` 尾巴(大小写无关,不动 `/v1`、`/v1beta/openai`),历史误填无需重填即自愈。

## [1.0.3] - 2026-06-19

后端 harness + 热路径系统性对抗审计(12 子系统,50 候选→26 确认→opus 核实)→ 22 项验证级增量修复(PATCH:全为缺陷修复,不重写架构)。真库 e2e 验证(迁移落库 + 单测,本批零新增失败)。

### Security
- **SSRF(high)**:GM LLM 热路径(`openai_compat.py`)此前用裸 `httpx.Client` 绕过 `_SsrfGuardTransport`,DNS rebinding 防护缺失(`base_url_override` user/admin 可控,写时闸过后 TTL 过期即可 rebind 到内网/元数据)。改走 `safe_httpx_client`(传输层 use-time 重解析;新增 `proxy` 形参,本地代理路径不丢失)。
- 锚点/回溯端点不再向客户端回传原始异常(含 SQL 片段)— 落服务端日志 + 通用文案(CWE-209)。

### Fixed
- harness `except Exception` 把上游 5xx/超时/401 误判为「特性不支持」→ 非幂等 POST 重复请求(重复计费)+ 掩盖真因;改为仅 HTTP 400 降级(64×500 抖动放大根因)。
- 模块重建 worker 缺 `finally` → DB 故障留僵尸 job;冷启动 DB 未就绪竞争致恢复/回收当轮不重试 → 加有界探活。
- DDL 连接无 `lock_timeout` → ALTER 撞长事务可挂起部署;新增 migration **v77** 把 v74 四表 `save_id/script_id` 由 `integer` 改 `bigint`(防 2^31 溢出)。
- RAG:换 embed provider 后召回侧用错 provider 的 key → 静默降级 ILIKE;`workers=2` 跨进程 embed-meta 缓存陈旧 → 加 TTL;第三方 openai 兼容 provider 错误 hint 不再被吞。
- 世界书 LLM 重建 `on conflict do nothing` 静默保旧 + 计数虚高 → `do update`(豁免 editor)+ 真实行数;生图「已取消」不再被失败/成功路径覆盖;同名 MCP 工具不再误路由到内部 dispatcher;登录码冷却不再计入已消费验证码;dashscope 首轮轮询计时修正。

## [1.0.2] - 2026-06-19 (@ 273d06214)

## [1.0.1] - 2026-06-19 (@ 11ddfb077)

## [0.5.0] - 2026-06-18 (@ c12b37518)

First SemVer release; baseline for desktop distribution + versioned releases.

### Added
- Temporal knowledge-base (剧情体验升级): new games follow the source novel more faithfully, gate spoilers by reached-anchor frontier, and advance progress by confirmed anchors (no over-shoot). New-games-only via `RPG_TKB_*` flags; existing saves unaffected. Import pipeline auto-builds reveal anchors so any new script is spoiler-gated.
- In-app update announcement: shown once on entry (reuses the disclaimer modal), never re-pops after seen, reopenable from the 使用须知 button.
- Version single-source-of-truth: root `VERSION`, `__APP_VERSION__` injected into the frontend, `app_version` exposed on `/api/health`, carried on feedback submissions.
- User feedback drawer history: users can see their submitted feedback and review status, including "adopted" acknowledgements after fixes are verified.
- Admin feedback replies: administrators can answer feedback, and users can read those replies in their feedback history.

### Changed
- Model selection is now per-user/per-save for normal users, while global catalog changes remain admin-only.
- Custom API credential entry is limited to supported providers for non-admin users to avoid unusable model/provider combinations.
- Game Console mobile side panels now open as a full-width bottom sheet with larger touch targets and horizontally scrollable tabs.
- Main GM output now defaults to a 4K token BYOK budget, with higher user-configurable headroom, so story replies are not cut off by the old strict cap.

### Fixed
- Retrieval no longer falls back to legacy local `.webnovel` / `indexes` sources when `script_id` is missing, keeping runtime recall on the database-backed path.
- Game Console stop signals now use restart-safe run identifiers and ignore stale database stop rows, so old manual-stop requests no longer interrupt later chat generations with "this round was interrupted".
- New game creation now blocks scripts whose import/rebuild job is still running or whose required chapters/timeline anchors are missing, so users cannot start a setup flow that would stall before selecting a starting point.
- Agent model selectors now allow manual model names for custom OpenAI-compatible credentials, so users can use providers whose `/models` endpoint is unavailable or incomplete.
- Script import now invalidates stale chapter-split previews when the file or rule changes, retries an expired preview upload once during confirm, shows cancellation as a clear terminal state, and auto-selects the best chapter split candidate when all rules score below 0.80.
- Local/self-hosted dev mode now accepts loopback frontend origins on dynamic Vite ports, so script import estimate/confirm requests no longer fail with "Origin not allowed" when the frontend falls back from 5173 to another localhost port.
- Self-hosted frontend bundles now treat an empty `<meta name="api-base" content="">` as an explicit same-origin API base, so login/schema requests no longer fall back to port 7860 when the backend serves `dist` on another local port.
- Fresh/self-hosted database setup now enables pgvector before versioned migrations, and migration v60 backfills missing vector columns and HNSW indexes so semantic retrieval works on both new and previously drifted databases.
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
- Game Console chat streaming now distinguishes completed streams, backend errors, idle timeouts, manual stops, and true premature closes, so normal SSE close events no longer show a false "generation interrupted" error and the failure card exposes retry plus event-log details.
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
