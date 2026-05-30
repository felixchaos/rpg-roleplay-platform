# Phase A — 提取重构(discover-then-link 五阶段管线)

> 实施级设计。替换 `rpg/chapter_fact_indexer.py` 的关键词匹配(根因)为
> **作者种子 + LLM 自举词表 + 逐章固定-schema 三元组(批量五折) + 两层消歧聚合 + 真向量**。
> 产出喂 Phase B 的规范层 KB(`docs/design/BC_kb_schema_worldtree.md`)。
>
> 现有锚点:`chapter_fact_indexer.build_chapter_facts()`(要替换的入口)、
> `chapter_facts` 表(复用为逐章原始三元组落地)、`script_import.py` 4e(调用点)、
> `import_jobs` 表(v9/v12 已有,提取作业状态持久化)。

---

## 1. 验收标准
- 二战书重提取后 `chapter_facts.concepts` 非空率 1.5%(13/866)→ **> 80%**
- 纪元 = 提取出的**常量** "2930 年代"(钉死,见 §3),GM 不再出 1935
- 实测单本成本落 **$1-2.5** 区间(§7);记录真实 token / 批量折扣
- 规范实体注册表去重后无明显重复人格(同人多别名归一)
- **铁律满足**:无任何一步让 LLM 对全局事件排序或推断纪元

---

## 2. 数据流(import 侧,作业化跑在 import_jobs 上)
```
script_chapters(已切净, exclude_from_extraction=false 的章)
  ↓ Pass 0  作者种子 + LLM 自举 → script_seed(纪元/世界线图) + entity_vocab(候选词表)
  ↓ Pass 1  逐章 LLM 固定-schema 三元组(batch API,五折) → chapter_facts(原始,per-chapter)
  ↓ Pass 2  两层消歧聚合 → kb_canon_entities / kb_canon_events / 时间线 / 世界书 / 规范世界线DAG
  ↓ Pass 3  自托管 BGE-M3 嵌入 → document_chunks.embedding(pgvector);检索切 pgvector
  ↓ Pass 4  关系图导出(实体→关系 DAG,供可视化 + 关系查询)
```
每 Pass 是 `import_jobs` 的一个 stage,可断点续跑;失败只重跑该 stage。

---

## 3. Pass 0 — 作者种子 + LLM 自举(解 bootstrap 死锁)

### 3.a 作者种子 `script_seed`(人填,治"从散文推纪元/多线"无解难题)
导入时(或可视化编辑器里)作者/管理员填一份**小种子**(全可选,但纪元强烈建议):
```json
{
  "era": {"label": "星历 2930 年代", "kind": "constant", "note": "全程钉死,GM 永不推断"},
  "power_system": ["神姬", "战姬", "..."],         // 力量体系公理 → constant 骨架
  "key_factions_seed": ["...", "..."],              // 给自举一个起点,不穷举
  "worldlines_seed": [                               // 决策1:默认单主线 + 弧
    {"id": "main", "label": "主线", "arcs": ["柏林暗流", "..."]}
  ]
}
```
- 没有种子也能跑(自举全自动),但**纪元种子是治 1935 的最便宜保险**。种子值进 §4.1 constant 骨架。

### 3.b LLM 自举词表 `entity_vocab`(discover-then-link 的 discover)
- 章节**滑窗采样**(不是全量;均匀抽 ~30-60 章 + 全部 author-flagged 重要章),frontier 模型做 NER:抽人物/派系/地点/概念候选 + 别名
- 聚合成 `entity_vocab`(候选实体词表 + 别名簇),作为 Pass 1 的"已知词表"喂下去——**先有词表,再逐章链**,破除"逐章提取需要一个尚不存在的词表"死锁
- 研究依据:RAKG/CREFT discover-then-link、滑窗+重叠(R2/SLIDE +24%/+39%)

---

## 4. Pass 1 — 逐章固定-schema 三元组(便宜模型 + Batch API)

### 4.a 每章 prompt 契约(输入)
- system:固定 schema 说明 + `script_seed.era`(钉死,要求"绝不推断别的纪元")+ `entity_vocab`(已知实体,要求"优先链已知,新实体显式标 proposed")
- user:本章正文(用 A.0 的 `content_descriptor` 而非怪标题)+ 前一章 1-2 句滚动摘要(给时序连续性,但**不要求全局排序**)

### 4.b 每章输出 schema(强制 JSON,落 `chapter_facts`)
复用现有 `chapter_facts` 表结构(characters/locations/factions/concepts/items/relationships/events 都是 jsonb),
但**值来自 LLM 三元组而非关键词计数**:
```json
{
  "chapter": 188,
  "story_time": {"label": "柏林暗流篇 中期", "relative_marker": "接上章次日", "era": "星历2930s"},
  "entities": [{"surface": "蕾穆丽娜", "canonical_guess": "蕾穆丽娜", "type": "character", "status": "proposed|linked", "evidence": "..."}],
  "events": [{"summary": "...", "participants": ["..."], "location": "...", "importance": 0-100, "causal_refs": ["前置事件描述"]}],
  "relationships": [{"from": "A", "to": "B", "kind": "敌对|盟友|...", "evidence": "..."}],
  "concepts": [{"name": "神姬", "gloss": "...", "evidence": "..."}],
  "confidence": 0-1
}
```
- **固定 schema 三元组**直接修"98% 空"(关键词匹配空,LLM 强制填字段)
- `importance` 是局部分(本章内),**不是**全局排序;`causal_refs` 是文本描述,Pass 2 才连图
- 模型:flash 级(便宜)。研究:逐章输出三元组不是散文

### 4.c 批量(降本核心)
- 866 章 → **一个 Batch API 提交**(Anthropic Batches / 等价),**五折**;异步轮询,落 `import_jobs` 进度
- 平台侧批量提取用平台 key 或公开书共享(§G 成本);私有重度提取走用户 BYOK

---

## 5. Pass 2 — 两层消歧 + 聚合(discover-then-link 的 link)

### 5.a 实体消歧(两层,降重复 ~45%)
1. **粗筛**:所有 surface/canonical_guess 过 BGE-M3 嵌入,余弦近邻聚簇(便宜,无 LLM)
2. **精判**:每个候选簇丢 frontier 模型判"是否同一实体"(小事实集,贵但量小);合并别名 → 一个 `logical_key`
- 产出 `kb_canon_entities(script_id, logical_key, name, aliases, type, summary, first_revealed_chapter, public_knowledge)`
- `first_revealed_chapter` = 该实体首次出现章(决策3 防剧透用);常识级实体标 `public_knowledge=true`

### 5.b 事件聚合 + 时间线(增量,不全局排序)
- 事件按 `story_time.label` + 章节顺序**增量**归并(章节本就有序,顺着推进,绝不让 LLM 重排全局)
- 产出真实 `script_timeline_anchors`(phase→章节范围,值来自 `story_time` 而非标题)
- 重大事件(高 importance + 高因果中心度)→ 升级为规范世界线节点候选(§5.d)

### 5.c 世界书(层次社区摘要)
- 实体关系图跑层次 Leiden 社区检测 → 每社区 LLM 摘要一条世界书 → `worldbook_entries`
- 标 `insertion_position`:核心设定(纪元/力量体系/主要派系)→ `'constant'`(§4.1 常驻);其余 → `'vector'`(检索)/`'keyed'`(关键词)

### 5.d 规范世界线 DAG(决策1:粗弧级,坐在细锚点上)
- **剧情节点检测**:事件评分 = `importance × 因果中心度`(下游 `causal_refs` 指向它的事件数)。取 top 节点,密度随章数缩放(~每 80-150 章一节点,主弧+子弧封顶 3-7)
- 产出 `script_worldlines`(默认单 `main` 线)+ `script_worldline_nodes`(每节点引用一簇章节/事件 + must_preserve/may_vary)
- **与现有 `save_anchor_states` 的关系**:后者由 `anchor_seed_agent` 从**每个** chapter event 细粒度播种(已存在);世界线节点是**粗层索引**,每节点聚合一组细锚点。GM 粗粒度引导、细粒度保真

---

## 6. Pass 3 / 4 — 嵌入 + 关系图
- **Pass 3**:章节块 + 实体/lore 卡 → 自托管 BGE-M3 → 写 `document_chunks.embedding`(pgvector,migration v10 已有列);**把 `retrieval.bm25_search` 的 LIKE 搜替换/补充为 pgvector 余弦检索**(保留 BM25 做混合检索更稳)
- **Pass 4**:`kb_canon_entities` + `kb_relationships` 导出关系 DAG(节点/边),供 §8 可视化 + GM `graph_neighbors` 查询

---

## 7. 成本(实测口径)
- Pass 1 逐章 flash + Batch 五折:$0.35-1 / 本(3.9M 字符)
- Pass 0 自举 + Pass 2 精判:frontier 但量小:$0.5-1
- Pass 3 嵌入:自托管 BGE-M3 ≈ 免费(自己 GPU/CPU)
- **合计 ≈ $1-2.5/本**(待 Phase A 实测确认)。公开书共享去重后人均摊薄(§G)
- 铁律:**绝不全程 frontier**(会飙 $15-30/本)

---

## 8. 落地改动清单
- **新建** `rpg/extract/seed.py`(Pass 0 种子+自举)、`rpg/extract/per_chapter.py`(Pass 1 + batch)、`rpg/extract/resolve.py`(Pass 2 消歧聚合)、`rpg/extract/embed.py`(Pass 3)、`rpg/extract/graph_export.py`(Pass 4)
- **改** `chapter_fact_indexer.py` — `build_chapter_facts` 内部改调新管线;**删** `_extract_fact`/`_rank_terms`/`_load_world`/`KEY_CHAPTER_TIME_LABELS` 等关键词逻辑 + 对 `indexes/world.json` 的依赖
- **改** `retrieval.py` — `bm25_search` 旁加 `vector_search`(pgvector),GM context 用混合
- **改** `script_import.py` 4e — 调新提取作业(挂 import_jobs,异步)
- **新表**(Phase B 篇详述):`kb_canon_entities` / `script_worldlines` / `script_worldline_nodes`;`worldbook_entries.insertion_position` 增 `'constant'`/`'vector'`;`script_chapters`/`chapter_facts` 复用
- **采用**:LlamaIndex PropertyGraphIndex 当 Pass 2 社区摘要 + 图脚手架(消歧层自建)

## 9. 测试 / 验证
- 单元:每章 schema 校验(JSON 必填字段非空率)
- 集成:二战书全量重跑,断言 concepts 非空率 > 80%、纪元常量 = 2930s、消歧后实体数合理
- 对照:抽 20 章人工核对提取三元组 precision/recall;GM 回归测纪元不再幻觉(Phase H)
