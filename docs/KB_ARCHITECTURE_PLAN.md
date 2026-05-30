# RPG 知识库 + 规范世界线引导 + 关系型世界树 + Agentic GM 架构 (v5 · 设计定稿)

> 经多轮设计对话收敛、并与**真实代码现状对账**后的实施级权威参考。
> v5 相对 v4 的两处实质变化:(1) 第 12 节 4 个开放决策已**拍板**(见下 §12);
> (2) 增加 §0.5「与现有代码的对账」——v4 误以为要从零建的世界树/锚点/工具框架**大部分已存在**,
> 本架构是在其上补齐提取质量 + 关系型 KB 层 + 有界 GM loop,不是推倒重来。
> **自包含 + 详细设计分篇**:本文件是索引与决策;每个 Phase 的实施级设计(DDL/函数签名/算法/IO 契约)在 `docs/design/*.md`。

---

## 0. 根因(为什么重构)
GM 把世界观 2930 年代套成 1935、编造不存在的"魔导师团"。根因链:
`chapter_fact_indexer._extract_fact 纯关键词匹配陈旧全局字典 indexes/world.json(6派系/3概念),从不读章节
→ chapter_facts 几乎全空(concepts 13/866)→ 世界书只6条 → 时间线只剩章节标题 → GM 无真实世界观 → 用参数化知识乱填`。
研究印证:bootstrap 死锁 + 让 LLM 推断纪元(全局排序崩溃)= 教科书级幻觉。
**额外发现**:866 章里 21.5%(186 章)是作者的话/卷末通知等非正文污染;标题多为玩梗无关
("804说好的爆发推迟了（75）");`chapter_splitter.py` 有 REMULINA 书本特化硬编码(针对某本书优化的反模式)。

---

## 0.5 与现有代码的对账(v5.1 已逐行核对修正)
> ⚠️ **v5 此表初版抄自 Explore 子代理报告、未亲自读码,把"全量快照 git"误称为可复用的关系型脊柱——错。**
> v5.1 已亲自读 `commits.py`/`init.py`/`chat_pipeline.py` 修正。证据列 = 我读过的 file:line。
> **关键纠正:存档世界树当前是「整份 state JSON blob/commit」(全量快照 git),不是关系型、不是行级 COW、存储 O(回合×整态)——是你设计的反面。**
> 关系型 KB 不是"挂层",是要把世界知识从 blob **迁出来**进关系表(见修正后 §2)。dispatcher/锚点/世界书确实接进了 live 链路(已核),但都往 blob 写。

| 能力 | 现状(已读码核对) | 证据 file:line | 结论 |
|---|---|---|---|
| 世界树**拓扑** | `branch_commits.parent_id` 自引用 = git 式 DAG;`branch_refs` 头指针;`runtime_checkouts` 每用户工作树 dirty | `init.py:128-146` | **拓扑可复用**(就是棵 commit 树) |
| 世界树**存储** | ⚠️ 每 commit 把**整份 `state.data`(player/world/relationships/memory/active_entities 全在内)塞进 `state_snapshot jsonb`**。`_insert_commit` 写 `Jsonb(snapshot)`。fork=整 blob 拷贝,存储 O(回合×整态) | `commits.py:96`、`init.py:663`、`state/core.py` | ❌ **与关系型设计冲突**。要把世界知识从 blob **迁出**进 `kb_*` 行级表,blob 降级(§2 修正) |
| 收束/锚点 | `save_anchor_states` + `mark_anchor_satisfied/superseded` + `anchor_seed_agent` 播种,**且 live 用**(save_phase_manager) | `command_tools_anchors.py`、`save_phase_manager.py` | **复用**。规范世界线 DAG(§4.4)粗弧层坐其上 |
| 工具框架 | `command_dispatcher` 真接 live(`/set` 路径 dispatch_sync;GM ops 走 `apply_structured_updates`),`ensure_registered` 在 startup 调,**非死代码** | `chat_pipeline.py:161,817`、`startup.py:89` | **复用**。但当前工具写进 **blob**,迁关系型后改写 `kb_*` 行 |
| 世界书 | `worldbook_entries`+overlays,**live 注入**(context_engine/gm master) | `agents/gm/master.py`、`context_engine/` | **复用**,新增 `insertion_position='constant'`(§4.1) |
| 时间线 | `script_timeline_anchors`(linear)+ `save_timeline_anchors` | `init.py:195`、migrations v14 | **复用**,但值来自标题(根因)→ §6 改真提取 |
| GM 写状态 | GM narration 出 `{op,path,value}` → `apply_structured_updates` 改 `state.data` blob | `chat_pipeline.py:817`、`state_op_tool_map.py` | 迁关系型后:同一入口改写 `kb_*` 行打 born_commit |
| 工具框架 | `command_dispatcher`(ToolSpec/ToolRegistry/scope=global|user|script|save/origins 白名单/per-save 锁/审计/MAX_TRACE_DEPTH=3/限流)~40 工具;`state_op_tool_map` 把 GM 的 `{op,path,value}` 映射成工具调用 | **复用**。GM 写 KB = 新增几个 save-scope 工具走同一 dispatcher(§7.3),天然审计 + COW |
| 聊天管线 | `chat_pipeline` 固定 5 阶段(directives→context→GM→apply ops→persist) | **升级**成有界 agentic loop(§7.1):GM 回合内可多轮查询/写工具直到 end_turn |
| 角色卡 | `character_cards` + `user_character_cards` | 复用 |
| 提取 | `chapter_fact_indexer`(关键词匹配) + `indexes/world.json`(陈旧字典) | **替换**(§6 discover-then-link)。这是根因所在 |
| 切分 | `chapter_splitter`(预设 + `_split_auto` + `_build_split_report` 报告框架 + REMULINA 硬编码 + 弱的 `_strip_pirate_promo`) | **升级**(§3 规则融合 + 三过滤),**删 REMULINA**,port `sanitize.ts` |
| 检索/向量 | 迁移 v10 已建 pgvector + `document_chunks.embedding`,但 `retrieval.bm25_search` 仍是 SQLite `vectors.db` 上的 LIKE 关键词搜 | **接通真向量**(§6 Pass3 自托管 BGE-M3 写 `document_chunks.embedding`,检索改 pgvector) |
| 可视化编辑器 | 无 | **新建**(§8,Phase E,非 MVP 阻塞) |

**一句话(修正)**:commit **拓扑**、收束、工具、世界书可复用,但**世界状态存储是全量 blob 快照,与你的关系型设计相反**——这是最大的真改造,不是"挂层"。本架构要做:① 干净的料(§3)② 真实提取(§6)③ **把世界知识从 state blob 迁进关系型 `kb_*` 行级层(§2,真迁移)** ④ 规范世界线粗 DAG(§4.4)⑤ 真向量检索(§6 Pass3)⑥ 有界 GM loop + 常驻骨架 + 防剧透/元知识(§7)⑦ 可视化编辑器(§8)。

---

## 1. 核心设计原则

### 1.1 两棵正交的树(最关键抽象)
| | 剧本枝叉 = 规范世界线 DAG | 存档分支 = 玩家世界树 |
|---|---|---|
| 是什么 | 作者写的既定走向(意图地图) | 玩家实际玩出的路径(实际足迹) |
| 范围/可变 | per-剧本,**钉死只读** | per-存档,动态 COW 版本化 |
| 用途 | GM 拿来引导的软锚点 | 记录玩家去了哪 |
| 物理 | `script_worldlines/_nodes`(新)+ 复用 `save_anchor_states` 作细锚点种子 | `branch_commits` DAG 脊柱(已有)+ 行级 KB 层 `kb_*`(新,带 born_commit) |

GM 用规范世界线 DAG 当软锚点,把玩家世界树引导回既定世界线。两者绝不混在一个机制。

### 1.2 其他原则
- **收束 + 局部自由**:结果可变、走向沿剧本枝叉(Steins;Gate 式,从单线升级成分支树)
- **三事一体**:GM 工具化写回 = 世界树 delta = 活态自洽维护(同一事三面);走现有 dispatcher = 自动审计
- **分层自主度**:聊天回合=有界 agentic loop / 提取=workflow 多代理+批量 / 后台世界=外部 cron 自主代理
- **结构靠人种子,血肉靠 LLM**:纪元/世界线地图作者填种子,LLM 挂章补血(避开"从散文推纪元/多线"无解难题)
- **摄入清洗确定性优先**:清洗/切分是结构性问题,确定性方法做 95%,LLM/嵌入只啃模糊 5%
- **复用优先于重写**(v5 新增):凡现有代码能用的脊柱/工具/表一律复用扩展,新代码只填真缺口

---

## 2. 数据模型:关系型世界树(把世界知识从 blob 迁进行级表,**真迁移,非挂层**)
**现状(已读码)**:`branch_commits` 是 commit DAG(拓扑可用),但**每 commit 存整份 `state.data` blob**——world/relationships/events/active_entities/memory 全在 blob 里(`commits.py:96`、`state/core.py`)。
fork=整 blob 拷贝,存储 O(回合×整态)。**这与你"关系型 + fork 零拷贝 + 存储 O(变化)"的设计正相反。**

**目标:`kb_*` 行级表 = 世界知识唯一真相源;blob 降级。** 不是并存两个真相源,而是迁移:
```sql
-- 拓扑复用:branch_commits(id, save_id, parent_id, turn_index, ...)  ← 保留 parent_id 树
-- 世界知识迁出 blob → 行级版本化表,每行打 born_commit(= 写入时 active commit_id)
kb_entities(id, save_id, born_commit, logical_key, name, type, status, summary, attrs jsonb, retired_at_commit, ...)
kb_events(id, save_id, born_commit, logical_key, story_time, summary, participants jsonb, location, retired_at_commit, ...)
kb_relationships(id, save_id, born_commit, logical_key, from_key, to_key, kind, note, retired_at_commit, ...)
kb_worldline_vars(id, save_id, born_commit, logical_key, value, retired_at_commit, ...)
```
- **一条枝叉完整时间线** = 沿该分支 commit 谱系(recursive CTE 取祖先集)取可见行,同 logical_key newest-wins(最大 born_commit 在祖先集内)。retired 行进祖先集则该 key 视为删除(tombstone)
- **fork = 零拷贝**(只新建 commit 指针,KB 行不动,自动继承祖先可见行);GM 改实体 = 只 INSERT 一行;分支天然隔离
- 存储 O(变化数);索引 `(save_id, logical_key, born_commit)`、`(save_id, born_commit)`。同 Dolt/Datomic/SQL:2011 system-versioned tables
- **blob 的下场(二选一,Phase C 实测定)**:(a) `state_snapshot` 退成**纯瞬时运行态**(scene/turn/dice 等非世界知识,且可由行级表重建)→ 不再是世界真相源;(b) 退成**检查点缓存**——由 `kb_*` 行物化生成,读加速用,丢了能重建。**世界知识不再以 blob 为准。**
- **这是真迁移**(写路径 `apply_structured_updates`/dispatcher 工具改写 `kb_*` 行;读路径 `resolve_world_view` 合并;blob 字段下线 + 数据搬迁脚本),不是新增独立层。详见 `docs/design/BC_kb_schema_worldtree.md`(已同步修正)。
- ⚠️ **风险/代价**:这是动 `state.data` 核心结构的高风险改造,牵连建档(`_build_initial_snapshot`)、持久化(`record_runtime_turn`)、读档、GM 上下文组装、所有现有读 `state.data.world/relationships` 的代码。Phase C 必须有迁移脚本 + 双读兼容期 + 回归测试,不能一刀切。

---

## 3. Stage -1:摄入与清洗(提取前置,规则融合自适应,**最先做**)
升级 `chapter_splitter.py` 成"规则库 + 结构性评分自适应选优 + 融合 + 三道过滤",零书本调参,**删 REMULINA**:
- **① 规则库(候选,删书本特化)**:复用 `RULE_PATTERNS` 预设 + **自派生规则**(扫全文找最频繁最一致疑似标记行派生专属正则,抓预设没覆盖的 `001.`/`【第X话】`)
- **② 结构性评分选优(自适应灵魂,无 LLM)**:序号连续性(最强,跳号=漏切)/ 长度均匀度(离群=多章粘连)/ 数量合理性 / marker 一致性 / 覆盖率 → 综合取最优(扩 `_classify_split_problem` 已有的指标)
- **③ 规则融合**:最优规则切主体 → 对离群超长块递归套次优规则找漏切子章 → 编号对账
- **④ 三道过滤(新)**:(a) 广告噪音(port `sanitize.ts` 取代 `_strip_pirate_promo`)(b) 作者非正文(标题模式+结构密度,抓 21.5% 污染)(c) 怪标题(标题vs内容嵌入相似度低→不依赖标题,生成内容描述符,原标题只显示)
- **⑤ 质量报告**(扩 `_build_split_report`)→ 可视化编辑器复核;不静默删
详细算法/评分公式/过滤规则/IO:`docs/design/A0_ingestion.md`。

---

## 4. 剧本规范层(per-script,钉死只读,提取建,图谱化)
### 4.1 世界观骨架(constant)— 治 1935 的核心
纪元+核心派系+力量体系+当前剧情弧 → **每轮常驻注入,从不检索**(constant 定义即不可条件触发)。
物理:`worldbook_entries.insertion_position='constant'` 的小集合(~300-600 token),prompt 缓存。纪元当**提取出的常量**钉死,GM 永不推断。
### 4.2 规范实体注册表
人物/派系/地点/概念,名+别名+类型+摘要+关系 → `kb_canon_entities`(per-script,见 §6 Pass2 产出)。
### 4.3 线性时间线锚点
真实 in-story 纪元/日期→章节范围→phase → 复用 `script_timeline_anchors`,但值来自真实提取而非章节标题。
### 4.4 规范世界线 DAG(灵魂,粗弧级)
作者既定世界线树,每条线一串**弧级**剧情节点(3-7/线);坐在 `save_anchor_states` 细锚点之上。
**重大剧情节点检测** = 事件重要度 + 因果中心度评分(下游依赖多者=节点),节点密度随小说体量缩放。
物理:新表 `script_worldlines` + `script_worldline_nodes`(每节点引用一簇 chapter events/细锚点)。
详细:`docs/design/BC_kb_schema_worldtree.md`。

---

## 5. 存档活态层(per-save,COW 关系型,分支版本化)
第 2 节 `kb_*` 行级表。这一周目发散现实:玩家创造的新实体/改变的 NPC 状态/原著没有的新事件/世界线分叉。
GM 走 dispatcher 结构化写=世界树 delta。读路径=规范层(钉死)∪ 当前分支节点活态库(newest-per-key,recursive CTE)。

---

## 6. 提取管线(import 侧,五阶段,研究验证,批量降本)
替换 `chapter_fact_indexer._extract_fact` 关键词匹配为 discover-then-link:
| Pass | 做什么 | 模型 | 批量 |
|---|---|---|---|
| 0 种子+自举 | 作者种子(纪元/世界线图)+ 章节滑窗 LLM NER 发现实体词表 | frontier 采样 | 否 |
| 1 逐章提取 | 便宜模型带词表读每章→固定 schema 三元组(实体/事件/时序/关系),链已知+提议新 | flash | **是(866请求→1batch,五折)** |
| 2 消歧+聚合 | 两层消歧(BGE-M3粗筛→LLM精判,降重复~45%)→规范实体;层次Leiden+LLM社区摘要→世界书;增量排时间线;挂规范世界线DAG | frontier 小事实集 | 部分 |
| 3 嵌入 | 章节块+实体/lore卡→自托管 BGE-M3→`document_chunks.embedding`(pgvector);检索改 pgvector cos | 自托管 | — |
| 4 关系图 | 实体→关系因果 DAG(供可视化+关系查询) | — | — |
研究依据(3-0):discover-then-link(RAKG/CREFT)、章节滑窗+重叠(R2/SLIDE +24%/+39%)、两层消歧、
固定schema三元组(修98%空)、层次Leiden社区摘要、增量update。**铁律**:永不让LLM全局排序/推纪元
(20+事件0%准确),纪元当种子钉死;逐章输出三元组不是散文。详细 schema/prompt/批量管线:`docs/design/A_extraction.md`。

---

## 7. GM serving:有界 agentic loop + 三层注入 + 规范世界线引导 + 抗污染/防剧透/元知识
- **有界循环**(Agent SDK 实证):升级 `chat_pipeline` 5 阶段为回合内 tool-calling loop;模型出 end_turn 自终止;`max_turns≈3`+`max_budget_usd≈0.05`(BYOK每迭代=用户掏钱);用户中断
- **三层注入**:① 常驻(世界观骨架 §4.1 + 当前场景 + 下一规范世界线锚点软目标;**预算 per-script 计算+prompt缓存+封顶~3K**)② 按需查询工具(search_canon/lookup_entity/lookup_timeline/lookup_lore/graph_neighbors,走 dispatcher script-scope)③ 结构化写工具=世界树delta(upsert_entity/record_event/set_worldline_var + 复用 mark_anchor_satisfied|superseded,走 dispatcher save-scope 审计)
- **规范世界线引导**:每轮 定位玩家最近哪条线/下一锚点→引导(锚点软目标注入)→放权(怎么达成交玩家)→重锚(偏到另一条规范枝叉切锚点,脱稿则即兴+尽量收束)。复用 `_list_pending_anchors` + drift_score
- **抗提示词污染(关键)**:① 影响因子量化(动作按爆炸半径分级,绝大多数局部→零污染)② 高影响动作触发**带外世界推演子代理**(隔离上下文算涟漪,写结构化delta,叙事GM看不到推理)③ 结构化状态非散文累积(世界树解析成紧凑现状)④ 检索而非注入
- **防剧透**:常驻lore+查询结果按玩家进度过滤(§12 决策3 已揭示集合);GM绝不让NPC表现得"知道玩家知道剧情"(除非设置允许)
- **元知识**:穿越者先知程度(无/部分/全知)+ NPC是否察觉异常先知 → 可切换设置,调节已揭示集合宽度
详细 loop 控制流/工具 schema/注入预算/污染分级/IO:`docs/design/D_gm_serving.md`。

---

## 8. 可视化图编辑器(god-mode,把人类变上帝)
提取产出**草稿图**(实体/事件/锚点/世界线/关系=节点/边)→ 人类在画布上精修:确认/合并实体、标/移锚点、
画世界线分叉、修关系、删噪声 → 精修后的图=GM消费的规范KB。把"txt→剧本"从黑箱变人在环可视化创作工具。
**这是采用图模型的理由**。摄入质量报告的 flag 项也在此复核。**MVP 先出只读复核表,画布 Phase E 跟进**(§12 决策4)。
详细:`docs/design/E_visual_editor.md`。

---

## 9. 创建引导 + 设置模型(重设计 NewGameModal)
分步向导,把影响体验的选择给用户(都有默认,高级展开):
1. 选剧本+起始世界线 2. 角色(卡/persona/出生点/身份)3. 元知识模式(先知程度+NPC察觉)
4. 引导强度(铁轨↔自由)+防剧透程度 5. 记忆模式/模型/权限(已有)
**游戏中可改 vs 锁死**:可改=引导强度/防剧透/记忆/模型/权限/NPC察觉;锁死(灾难性)=剧本/起始世界线/
角色身份。锁死项灰显+提示。详细:`docs/design/F_onboarding_settings.md`。

---

## 10. 存储与运营成本
**决定性:玩法 BYOK→平台零经常性推理成本;唯一变量=一次性、可共享、可配额的提取。**
- 提取单本3.9M字符(待实测):逐章flash+**批量五折**$0.35-1 + 全局frontier$0.5-1 + 自托管嵌入≈免费 = **≈$1-2.5/本**
- 世界树存储:行级 KB delta(§2)+ 检查点,1000回合/50分支≈MB级;剧本规范层per-script存一次共享;版本化零LLM成本
- 免费档5杠杆:模型分层/自托管嵌入/公开书KB共享去重/月配额/重度BYOK-提取;import硬token上限
- 启动100用户:人均2-3本≈$400-1200一次性,因共享大概率<$100
- 铁律:提取必须分层用模型(全程frontier飙$15-30/本崩);公开书必须共享去重。详细:`docs/design/G_ops_cost.md`。

---

## 11. 自建 vs 采用
- **采用**:LlamaIndex GraphRAG V2/PropertyGraphIndex 当提取+图+社区摘要脚手架;BGE-M3 自托管嵌入
- **必须自建**(研究明确cookbook没有):① 实体消歧/去重层 ② in-story纪元/世界线锚定
- **自建**:规则融合自适应切分、规范世界线DAG引导、关系型世界树COW(在现有 commit DAG 上)、GM查询+写工具、可视化图编辑器、章节进度过滤、元知识/引导设置
- **图谱:采用**;其余纯 PostgreSQL,不上独立图DB引擎

---

## 12. 开放决策 — 已拍板(v5)
> 用户授权自行决定。四项定稿如下,已贯穿全文与各 design 篇。

**决策 1 — 世界线种子粒度**:**单条主线先行(MVP),schema 支持 N 条分支世界线。** 每线 3-7 个**弧级**剧情节点(importance+因果中心度自动检测,密度随章数缩放,约每 80-150 章一节点、主弧+子弧封顶 7)。规范世界线 DAG 是**粗弧层**,坐在已有 `save_anchor_states` 细事件锚点之上——GM 粗粒度引导、细粒度保真。作者可在可视化编辑器加显式分支线;提取默认主线+弧 phase。理由:提取成本有界、GM 有软目标、与已有细锚点分层不冲突。

**决策 2 — constant 注入触发**:**混合,常驻骨架不检索 + 长尾向量检索。** 一个永远在场的"constant spine"(纪元/年代、世界根本规则、力量体系公理、当前弧,~300-600 token)**每轮无条件注入并 prompt 缓存**——这正是治 1935 的关键(constant 按定义不可条件触发)。其余实体/派系/地点 lore 走 pgvector 按需检索。物理:`worldbook_entries.insertion_position` 增 `'constant'`(常驻)对比 `'keyed'`(关键词触发)对比 `'vector'`(检索)。理由:"常量"不能条件触发;检索只服务长尾。

**决策 3 — 章节进度过滤(防剧透)**:**"已揭示集合"。** 每条规范 KB 行(实体/事件/lore/世界线节点)带 `first_revealed_chapter int`;每存档track `progress_chapter`(玩家故事已达的最大规范章);KB 查询过滤 `first_revealed_chapter <= progress_chapter`。玩家自造的活态行不过滤(是玩家自己的)。另设 `public_knowledge bool` 标常识级(不论进度)。**元知识设置调节集合宽度**:先知=无→严格进度;部分→进度+标记的"著名未来事件";全知→不过滤。理由:一次 int 比较、极廉、与元知识天然组合。

**决策 4 — 可视化图编辑器是否核心**:**图数据模型是核心(KB 本就是图,是底座);画布 UI 是 Phase E,不阻塞 MVP。** 顺序:先打通 提取→KB→GM(用最小化只读复核表 + 现有 `import_report` 做 QA);画布编辑器作为 fast-follow 让 god-mode 编辑顺手。承诺为路线图功能因为:(a) 是用户愿景("类似上帝")(b) 是提取纠错的 QA 面(质量报告 flag 路由到这)(c) 把产品抬成"可视化创作平台"差异化。理由:别让幻觉修复卡在前端画布;但保留图模型,编辑器后加无返工。

---

## 13. 诚实缺口(研究 caveats,保留)
① 论文非百万字网文验证,模式高置信迁移但数字须自测 ② 自动定纪元/多线从散文提取无现成方案→必须作者种子
③ 成本是推断,PhaseA先实测 ④ 推理模式不"消除"时序错误→必须钉纪元数据 ⑤ 行级 KB 层与 full-snapshot 并存的读路径合并需实测延迟(recursive CTE on 50 分支)

---

## 14. 实施路线图(待办;每阶段数据/浏览器验证才算完成)
- **Phase A.0 — Stage -1 摄入清洗(最先!)** `docs/design/A0_ingestion.md`:规则融合自适应切分+三道过滤+质量报告。删 REMULINA。port sanitize.ts。二战书实测:非正文污染21.5%→<2%、漏切归零
- **Phase A — 提取重构** `docs/design/A_extraction.md`:作者种子+Pass0自举+Pass1逐章LLM三元组(批量)+Pass2消歧聚合。实测成本+concepts非空率1.5%→>80%
- **Phase B — 规范层KB(图谱化)** `docs/design/BC_kb_schema_worldtree.md`:`kb_canon_*`+真实时间线+规范世界线DAG(剧情节点检测)+constant骨架+BGE-M3向量接通 pgvector
- **Phase C — 关系型世界树** 同上篇:`kb_*`行级层(born_commit/COW,挂现有 commit DAG)+谱系recursive CTE读路径+检查点+GC
- **Phase D — GM serving** `docs/design/D_gm_serving.md`:有界agentic loop+三层注入(锚点软目标)+查询/写工具走dispatcher+抗污染(影响因子+带外推演)+防剧透(已揭示集合)+元知识
- **Phase E — 可视化图编辑器** `docs/design/E_visual_editor.md`:先只读复核表→前端画布god编辑;摄入flag复核
- **Phase F — 创建引导+设置** `docs/design/F_onboarding_settings.md`:分步向导+可改/锁死设置模型
- **Phase G — 运营护栏** `docs/design/G_ops_cost.md`:批量提交对齐 import_jobs/配额/公开书共享去重/成本上限/模型分层
- **Phase H — 回填验证**:重提取二战书:纪元2930正确、神姬/战姬常驻、规范世界线引导生效、世界树分支隔离、GM不幻觉

---

*v5 设计定稿。4 决策已拍板,与真实代码对账完成,详细设计分篇落 `docs/design/`。
下一步实施从 Phase A.0(摄入清洗)动手——先治脏数据再提取。*
