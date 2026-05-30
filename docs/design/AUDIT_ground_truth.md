# 地面真相审计(逐行读码,非子代理转述)

> v5 的 §0.5 对账表是抄 Explore 子代理报告写的、未读码,出了错。本篇是**亲自逐文件读码**后的核实结果。
> 每条带 `file:line` 证据 = 我实际打开读过的。置信度分:**深读**(读了完整逻辑)/ **核到 wiring**(确认接线但没全跟行为)。
> 结论列直接决定设计该信哪条、改哪条。

---

## 1. 存档世界树:全量 blob 快照,**与关系型设计冲突**(深读)
| 项 | 真相 | 证据 |
|---|---|---|
| 存储 | 每 commit 把**整份 `state.data`** 塞进 `branch_commits.state_snapshot jsonb`;world/relationships/events/active_entities/memory 全在 blob 内 | `commits.py:96 Jsonb(snapshot)`、`init.py:663`、`state/core.py:163 DEFAULT_STATE`(:229 active_entities :256 world :269 relationships :299 memory) |
| 分支 | fork = 整 blob 拷贝;存储 O(回合×整态);拓扑靠 `parent_id` 自引用 | `init.py:128-146`、`commits.py:34` |
| 写路径 | 玩家 `/set` 与 GM `{op,path,value}` 都改内存 `state.data`,再整份快照落库 | `chat_pipeline.py:161`(/set dispatch)、`:817 apply_structured_updates` |
**结论:❌ 与你"关系型 + fork 零拷贝 + O(变化)低成本"正相反。** 不是挂层,是把世界知识从 blob 迁出进 `kb_*` 行级表、blob 降级的**真迁移**。已改根 §2 + BC 篇 §1。

## 2. 提取根因:关键词匹配 + bootstrap 死锁(深读,确认且更精确)
| 环节 | 真相 | 证据 |
|---|---|---|
| 根因函数 | `_extract_fact` 纯关键词:`concepts=_rank_terms(body, known_concepts)`、`factions=_rank_terms(body, 全局world.json的key_factions)` | `chapter_fact_indexer.py:302-303` |
| 平台**也**用它 | 平台 import 不是跑离线 CLI,而是 `knowledge._chunks` **直接 import 同一个 `_extract_fact`** | `_chunks.py:8 from chapter_fact_indexer import _extract_fact`、`:129 调用` |
| concepts 为何全空 | import 走 `rebuild=True` → `world={}` → `known_concepts=[]`/`known_locations=[]` → `_rank_terms(body, [])` 恒空 | `session.py:250` 内 `world = {} if rebuild else _load_world(...)`、`script_import.py:360 rebuild=True` |
| factions 为何只 6 | line 303 `_load_world()` **不带 script_id**,读全局 `indexes/world.json`(实测 key_factions=6、key_concepts=3、**无 year 字段**) | `world.json`、`chapter_fact_indexer.py:303` |
| bootstrap 死锁 | 代码注释自陈:必须先插 chapter_facts 再从 facts 聚合 character_cards(词表与提取互为前提) | `session.py` task40 注释 |
| 1935 从哪来 | world.json 只说"近未来",**没有纪元年份** → GM 无锚 → 参数化乱填 1935 | `world.json setting` |
**结论:✅ 根因方向(关键词匹配 + 死锁 + 无纪元锚)完全坐实,且比之前更精确。** discover-then-link + 钉纪元的修法对路。

## 3. ⚠️ 设计写错的入口(我 v5 的笔误,需改设计文档)
| 设计里写的 | 真相 | 证据 |
|---|---|---|
| "改 `build_chapter_facts` 内部" | `build_chapter_facts` 只被自己 CLI `__main__` 调,读 `正文/*.md` 写 **SQLite** `.webnovel/chapter_facts.db`——**离线单本工具,平台不用** | `chapter_fact_indexer.py:671`(唯一调用点)、`:55-57` 路径 |
| "`script_import.py` 4e 调提取" | 平台提取入口是 `knowledge.sync_script_knowledge`(后台异步,rebuild) | `script_import.py:360`、`session.py:250` |
| 要替换的编排 | `knowledge/_sync.py` + `_chunks.py`(写 postgres chapter_facts/worldbook/character_cards),不是 build_chapter_facts | `_chunks.py:64`、`_sync.py:132` |
**动作:已改 `A_extraction.md` 把入口指向 `sync_script_knowledge`/`_chunks`/`_sync`。**

## 4. 世界书种子仍依赖陈旧 world.json(深读)
- 这本书的 worldbook 走"路径2 老格式兼容",从 `world.json` key_factions/key_concepts 灌,stamp `source: indexes/world.json` | `_utils.py:142-170`、`_sync.py:168`
- **结论:✅ "世界书只 6 条"坐实**,根在 world.json 兼容路径。§6 改真提取后这条退役。

## 5. 检索:BM25 LIKE on SQLite,仅默认书(深读)
- `bm25_search` 连 `.webnovel/vectors.db`(SQLite),`content LIKE ?` 关键词匹配;且注释明说"仅默认走(MuMu 原著 chunks)" | `retrieval.py:94,113,120,641`
- pgvector(migration v10 建的 `document_chunks.embedding`)**没接进检索**
- **结论:✅ "真向量没接通"坐实。** §6 Pass3 接 pgvector 对路。

## 6. 切分:REMULINA 书本特化硬编码(深读)
- 31 处 REMULINA;`_should_use_remulina_special`(:385)在 `_split_chapters_internal`(:194)里抢先分流到 `_split_remulina_novel` | `chapter_splitter.py:77-89,194,385`
- **结论:✅ 反模式坐实。** §3 删除 + 规则融合对路。

## 7. 可复用项(核到 wiring,行为未全跟)
| 项 | 状态 | 证据 |
|---|---|---|
| 收束锚点 | ✅ 真表 + 真工具 + 注册 + live 用 | `migrations:306 save_anchor_states`(is_fatal/drift_score)、`command_tools_anchors.py:292/323/355`、`register.py:350`、`save_phase_manager.py` |
| 工具 dispatcher | ✅ 接 live 非死代码 | `chat_pipeline.py:161,817`、`startup.py:89 ensure_registered` |
| 世界书注入 | ✅ wiring 在(深度未全跟) | `context_engine/`、`agents/gm/master.py` |
| commit 拓扑 | ✅ parent_id 树可复用(但存储是 blob,见 §1) | `init.py:128` |
**结论:这些"复用"判断成立**(锚点/dispatcher/拓扑),与世界树存储是两回事——前者能用,后者要迁。

---

## 总评:v5 设计对不对?
- **方向全对**:关键词→discover-then-link、钉纪元、删 REMULINA、接 pgvector、关系型世界树、规范世界线引导、复用 dispatcher/锚点 —— 这些经核都成立。
- **两处实质错(已改)**:① §0.5 把 blob 快照误称可复用关系型脊柱(§1)② 提取入口写成离线 `build_chapter_facts` 而非平台 `sync_script_knowledge`(§3)。
- **教训**:§0.5 整表当初没读码、抄子代理 —— 这是错误来源。本篇所有结论可凭 file:line 复核。
