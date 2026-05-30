# Phase B + C — 规范层 KB(图谱)+ 关系型世界树(行级 COW)

> 实施级 schema 设计。两件事:
> **B** = per-script 钉死只读的规范知识(实体注册表 + 真时间线 + 规范世界线 DAG + constant 骨架);
> **C** = per-save 动态 COW 行级世界树(玩家发散的活态 KB),挂在**已存在的** `branch_commits` 提交 DAG 上。
>
> 核心复用:`branch_commits(id, save_id, parent_id, turn_index, state_snapshot jsonb, ...)` 已是 git 式 DAG,
> 提供世界树**脊柱**;`save_anchor_states` 已是细粒度收束锚点。本篇只新增**行级 KB 层 + 粗世界线 DAG**。

---

## 1. 设计要点(为什么这样切)
- **规范层(B)只读、per-script、可共享**:一本书提取一次,所有玩它的存档共享(成本摊薄,§G)
- **活态层(C)可写、per-save、行级版本化**:玩家改的世界,COW,沿 commit 谱系隔离分支
- **复用 commit 拓扑**:`branch_commits.parent_id` 树已有(`init.py:128`)。C 层每行挂一个 `born_commit`(= 写入时 active commit_id),不新建第二套树
- ⚠️ **关键修正(读码后)**:`branch_commits.state_snapshot` 当前存的**不是**轻量运行态——它存**整份 `state.data`**,world/relationships/events/active_entities/memory 全在里面(`commits.py:96`、`state/core.py`)。所以这不是"加层"而是**迁移**:把世界知识字段从 blob **搬进** `kb_*` 行级表,`kb_*` 成**唯一真相源**,blob 退成瞬时运行态或可重建检查点缓存(见根 §2)。**不允许 blob 与 kb_* 双写同一世界知识**(两个真相源 = 必然漂移)。
- **读 = 规范层(钉死)∪ 活态层(当前分支 newest-per-key)**,活态覆盖规范(玩家改了 NPC 状态以玩家版为准)
- **迁移代价(已认)**:动 `state.data` 核心结构,牵连 `_build_initial_snapshot` / `record_runtime_turn` / 读档 / GM 上下文 / 所有读 `state.data.world|relationships` 处。需迁移脚本 + 双读兼容期 + 回归(§5)。这是 Phase C 的真实体量,不是"挂个表"。

---

## 2. Phase B — 规范层 schema(per-script,只读)

### 2.1 规范实体注册表 `kb_canon_entities`
```sql
CREATE TABLE kb_canon_entities (
  id              bigserial PRIMARY KEY,
  script_id       bigint NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
  logical_key     text   NOT NULL,              -- 消歧后稳定主键(Pass2 产出)
  name            text   NOT NULL,
  aliases         jsonb  NOT NULL DEFAULT '[]',
  type            text   NOT NULL,              -- character|faction|location|concept|item
  summary         text   NOT NULL DEFAULT '',
  attrs           jsonb  NOT NULL DEFAULT '{}', -- 类型特定属性(身份/外貌/力量…)
  first_revealed_chapter int NOT NULL,          -- 决策3 防剧透
  public_knowledge bool  NOT NULL DEFAULT false,-- 常识级,不受进度过滤
  importance      int    NOT NULL DEFAULT 0,    -- 用于注入预算排序
  embedding       vector(1024),                 -- BGE-M3,pgvector 检索
  metadata        jsonb  NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE(script_id, logical_key)
);
CREATE INDEX ON kb_canon_entities (script_id, type);
CREATE INDEX ON kb_canon_entities USING ivfflat (embedding vector_cosine_ops);
```

### 2.2 真实时间线 — 复用 `script_timeline_anchors`
不新建表,但**值来自 Pass2 真提取**(`story_time.label`)而非章节标题。纪元字段进 §2.4 constant。

### 2.3 规范世界线 DAG `script_worldlines` + `script_worldline_nodes`(决策1:粗弧级)
```sql
CREATE TABLE script_worldlines (
  id          bigserial PRIMARY KEY,
  script_id   bigint NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
  wl_key      text   NOT NULL,                  -- 'main' | 'branch:xxx'
  label       text   NOT NULL,
  parent_wl   text,                             -- 世界线分叉:从哪条线在哪个节点分出
  branch_at_node text,                          -- 分叉点(parent_wl 的某 node_key)
  is_primary  bool   NOT NULL DEFAULT false,    -- 默认主线
  source      text   NOT NULL DEFAULT 'extracted', -- extracted|author_explicit
  metadata    jsonb  NOT NULL DEFAULT '{}',
  UNIQUE(script_id, wl_key)
);
CREATE TABLE script_worldline_nodes (
  id            bigserial PRIMARY KEY,
  script_id     bigint NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
  wl_key        text   NOT NULL,
  node_key      text   NOT NULL,                -- 线内有序节点标识
  seq           int    NOT NULL,                -- 线内顺序
  label         text   NOT NULL,                -- 弧级剧情节点名
  summary       text   NOT NULL DEFAULT '',
  chapter_min   int, chapter_max int,           -- 该节点覆盖的章节簇
  anchor_keys   jsonb  NOT NULL DEFAULT '[]',   -- 引用的细 save_anchor_states anchor_key 簇
  must_preserve jsonb  NOT NULL DEFAULT '[]',   -- 收束硬约束(对齐 save_anchor_states 语义)
  may_vary      jsonb  NOT NULL DEFAULT '[]',
  causal_centrality real NOT NULL DEFAULT 0,    -- 节点检测分
  first_revealed_chapter int NOT NULL,
  UNIQUE(script_id, wl_key, node_key)
);
```
- 默认提取出**一条 `main` 线 + 3-7 弧节点**;作者可在编辑器加 `branch:*` 线(`parent_wl`+`branch_at_node` 描述分叉)
- 节点的 `anchor_keys` 把粗节点和已有细 `save_anchor_states`(由 `anchor_seed_agent` 播种)绑定 → GM 粗引导、细保真

### 2.4 constant 骨架 — 复用 `worldbook_entries` 加模式(决策2)
不新建表,给 `worldbook_entries` 的 `insertion_position` 增枚举值:
- `'constant'` = 每轮无条件注入 + prompt 缓存(纪元/力量体系公理/主要派系/当前弧;~300-600 token 上限,§D 预算)
- `'keyed'` = 现有关键词触发(keys/regex_keys)
- `'vector'` = pgvector 按需检索
迁移:`worldbook_entries` 加 CHECK 或就用 text;Pass2 写入时给核心设定打 `'constant'`。

---

## 3. Phase C — 关系型世界树 schema(per-save,COW 行级)

### 3.1 活态 KB 行级表(挂 commit DAG)
```sql
CREATE TABLE kb_entities (
  id            bigserial PRIMARY KEY,
  save_id       bigint NOT NULL REFERENCES game_saves(id) ON DELETE CASCADE,
  born_commit   bigint NOT NULL REFERENCES branch_commits(id),  -- 写入时 active commit
  logical_key   text   NOT NULL,                -- 与 kb_canon_entities.logical_key 对齐(覆盖规范)
  name          text   NOT NULL,
  type          text   NOT NULL,
  status        text   NOT NULL DEFAULT 'active',
  summary       text   NOT NULL DEFAULT '',
  attrs         jsonb  NOT NULL DEFAULT '{}',
  retired_at_commit bigint REFERENCES branch_commits(id),       -- tombstone(删除标记)
  origin        text   NOT NULL DEFAULT 'player',-- player(新造) | canon_override(改规范实体)
  metadata      jsonb  NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON kb_entities (save_id, logical_key, born_commit);
CREATE INDEX ON kb_entities (save_id, born_commit);
-- kb_events / kb_relationships / kb_worldline_vars 同构(见 §3.2)
```
```sql
CREATE TABLE kb_events (
  id bigserial PRIMARY KEY, save_id bigint NOT NULL, born_commit bigint NOT NULL,
  logical_key text NOT NULL, story_time text, summary text NOT NULL,
  participants jsonb DEFAULT '[]', location text, retired_at_commit bigint, metadata jsonb DEFAULT '{}');
CREATE TABLE kb_relationships (
  id bigserial PRIMARY KEY, save_id bigint NOT NULL, born_commit bigint NOT NULL,
  logical_key text NOT NULL, from_key text, to_key text, kind text, note text, retired_at_commit bigint);
CREATE TABLE kb_worldline_vars (
  id bigserial PRIMARY KEY, save_id bigint NOT NULL, born_commit bigint NOT NULL,
  logical_key text NOT NULL, value jsonb, retired_at_commit bigint);
```
`logical_key` = 逻辑实体/事件/关系/变量的稳定身份;**同 key 多行 = 历史版本**,newest-in-ancestry wins。

### 3.2 读路径:沿分支谱系取可见行(recursive CTE)
"当前分支的世界现状" = 从 active commit 沿 `parent_id` 回溯得**祖先 commit 集 A**,
对每个 `logical_key` 取 `born_commit ∈ A` 中**最大 born_commit** 的那行;若那行 `retired_at_commit ∈ A` 则视为删除。
```sql
-- 给定 :active_commit, :save_id
WITH RECURSIVE ancestry(cid, depth) AS (
    SELECT :active_commit, 0
  UNION ALL
    SELECT bc.parent_id, a.depth+1
    FROM branch_commits bc JOIN ancestry a ON bc.id = a.cid
    WHERE bc.parent_id IS NOT NULL
),
visible AS (
  SELECT e.*,
         row_number() OVER (PARTITION BY e.logical_key ORDER BY e.born_commit DESC) AS rn
  FROM kb_entities e
  WHERE e.save_id = :save_id
    AND e.born_commit IN (SELECT cid FROM ancestry)
)
SELECT * FROM visible
WHERE rn = 1
  AND (retired_at_commit IS NULL OR retired_at_commit NOT IN (SELECT cid FROM ancestry));
```
- **fork = 零拷贝**:新分支只是新 commit 指 parent,`kb_*` 行不动 → 自动继承祖先可见行
- **写 = INSERT 一行**打当前 commit 戳(永不 UPDATE 既往行)→ 天然 COW + 审计
- **删 = INSERT tombstone**(retired)或给该 key 写 `retired_at_commit`
- 性能:`ancestry` 深度 = 回合数;`(save_id, logical_key, born_commit)` 索引支撑;50 分支/1000 回合实测延迟(§验收)。慢则加**检查点**(§3.3)

### 3.3 检查点 + GC
- **检查点**:每 N 回合或分叉点,把当前可见集物化成一行 `kb_checkpoints(save_id, commit_id, snapshot jsonb)`;读时从最近检查点 + 增量祖先,缩短 CTE
- **GC**:被所有 ref 抛弃(不可达)的孤儿 commit 链对应的 `kb_*` 行可回收(复用现有 commit GC 逻辑若有,否则新增 mark-sweep)

### 3.4 GM 写 = 走 dispatcher(三事一体)
GM 的世界改动**不直接 SQL**,而是调 save-scope 工具(§D),工具内部 INSERT `kb_*` 行打当前 commit 戳。
→ 复用 `command_dispatcher` 的审计/锁/origin 白名单;GM 写回 = 世界树 delta = 活态自洽,同一事。

---

## 4. 落地改动清单
- **新迁移**(`platform_app/db/migrations.py` 新版本号):建 `kb_canon_entities` / `script_worldlines` / `script_worldline_nodes` / `kb_entities` / `kb_events` / `kb_relationships` / `kb_worldline_vars` / `kb_checkpoints`;`worldbook_entries.insertion_position` 扩枚举;pgvector 索引
- **新建** `rpg/kb/canon_repo.py`(规范层读)、`rpg/kb/live_repo.py`(活态层读写 + recursive CTE + 检查点)
- **改** Pass2(`rpg/extract/resolve.py`)写规范层表
- **复用**:`branch_commits` / `branch_refs` / `runtime_checkouts` / `save_anchor_states` 全部不动,只新增挂载
- **读合并** `rpg/kb/view.py::resolve_world_view(save_id, commit_id, progress_chapter, meta_knowledge_mode)` = 规范层(进度过滤)∪ 活态层(newest-per-key),供 §D GM 注入与查询工具共用

## 5. 验收 / 测试
- 单元:同 logical_key 多版本,recursive CTE 取 newest-in-ancestry 正确;tombstone 生效;fork 后两分支互不可见对方写入
- 性能:50 分支 × 1000 回合,`resolve_world_view` p95 延迟达标(目标 < 50ms,超则启用检查点)
- 集成:GM 调写工具 → `kb_*` 新行带正确 born_commit → 切分支读到隔离世界
