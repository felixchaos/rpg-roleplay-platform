# Phase A.0 — Stage -1 摄入与清洗(规则融合自适应切分 + 三道过滤)

> 实施级设计。目标:把 `chapter_splitter.py` 从"预设 + REMULINA 硬编码 + 弱广告过滤"升级成
> **规则库 + 结构性评分自适应选优 + 融合 + 三道过滤 + 质量报告**,零书本调参。
> 这是整条提取链的"第 0 步":脏数据进 = 幻觉出。先治料。
>
> 现有锚点:`rpg/chapter_splitter.py`(993 行,`ChapterSplitter` 类)、
> `rpg/platform_app/script_import.py::import_script`(4a decode → 4b clean → 4c split → 4d sync → 4e facts)、
> 外部成熟资产 `/Users/felix/program/Stellatrix/books/server/utils/sanitize.ts`。

---

## 1. 验收标准(Phase A.0 完成定义)
在二战书(script 36264,现 866 章)上实测:
- 非正文污染(作者的话/卷末通知)从 21.5%(186/866)→ **< 2%**,且被标注而非静默删
- 漏切(超长块内含未识别子章)→ **0**(编号对账无跳号缺口)
- 每章带 `title_confidence`;低可信标题生成内容描述符,**不污染**下游提取
- 全程**无任何书名/书本特化分支**(删 REMULINA_*),换任意网文 txt 不退化
- 产出结构化**质量报告**喂给复核 UI(Phase E),不静默丢数据

---

## 2. 总流程(替换 `_split_chapters_internal` 的调度)
```
decode_bytes(raw) → text, encoding              # 复用现有
  ↓
clean_corpus(text) → text', cleaning_report     # 新:port sanitize.ts(广告/乱码,§4.a)
  ↓
candidates = build_candidate_rules(text')       # 新:预设(删REMULINA) + 自派生(§3.1)
  ↓
for rule in candidates: segs = apply(rule); score = structural_score(segs, text')   # §3.2
  ↓
best, runnerup = top2_by_score(scored)
  ↓
chapters = fuse(best, runnerup, text')          # 主切 + 离群块递归次优 + 编号对账(§3.3)
  ↓
chapters = filter_non_content(chapters)         # 新:作者非正文标注(§4.b)
chapters = annotate_weird_titles(chapters)      # 新:怪标题→内容描述符(§4.c)
  ↓
report = build_split_report_v2(chapters, ...)   # 扩现有报告(§5)
return chapters, report
```
**关键:确定性方法做 95%。** 嵌入只在 §4.c 怪标题判定用一次(且可降级为纯启发式)。LLM 完全不进 A.0。

---

## 3. 规则融合自适应切分(无 LLM)

### 3.1 候选规则库 `build_candidate_rules(text) -> list[Rule]`
`Rule = {id, kind: 'preset'|'derived', regex, heading_test, expected_density}`
- **预设**(复用 `RULE_PATTERNS` + `STRONG_CHAPTER_PATTERNS`,**删 REMULINA_* 全部**):
  - `chapter_cn` `第[一二三…0-9]+章`、`corpus` 语料、`chapter_en` `Chapter N`、`number_dot` `^\d+\.`、`paren_num` `（\d+）`、`卷-章` `第X卷…第Y章`、`话` `【第X话】`/`第X话`、`序章/楔子/尾声/番外`
- **自派生规则**(新,抓预设没覆盖的本书约定):
  1. 扫全文逐行,抽"疑似标记行"= 短(< 40 字)+ 含序号 token(汉数/阿拉伯/罗马)+ 行首近左边
  2. 按"标记骨架"聚类(把序号位抽象成 `\d+`/`[一二三…]+`,例 `第3章` 与 `第188章` 同骨架)
  3. 取**出现频次最高 + 序号最连续**的骨架 → 反编译成正则当一条 derived rule
  - 这样 `001.`、`【第X话】`、`(75)` 这类作者自创约定也能被抓为候选,无需预设穷举

### 3.2 结构性评分 `structural_score(segs, text) -> float`(自适应灵魂)
对每条候选规则切出的段集打分,五维加权(权重见注):
| 维度 | 计算 | 直觉 |
|---|---|---|
| **序号连续性** (0.35) | 提取每段标题序号,算最长连续递增覆盖率 = 连续序号数 / 期望序号数;**跳号 = 漏切**重罚 | 最强信号 |
| **长度均匀度** (0.20) | `1 - clip(size_cv, 0, 1)`,size_cv = 段长标准差/均值;**离群超长 = 多章粘连** | 复用现有 `size_cv` |
| **数量合理性** (0.15) | 段数落在 `[total_words/8000, total_words/1500]` 区间得满分,外部线性衰减 | 网文每章 1.5k-8k 字 |
| **marker 一致性** (0.15) | 所有标题命中同一骨架的比例 | 杂牌标题=切错 |
| **覆盖率** (0.15) | 被分进某章的正文字符 / 总字符;孤儿文本(段间漏)扣分 | 防漏首尾 |
- 权重是默认,落 `SPLIT_SCORE_WEIGHTS` 常量便于调;**不针对书调,针对"切分质量"这一普遍属性调**
- 扩展现有 `_classify_split_problem` 的指标(已有 size_cv/heading_density/abnormal_chapter_numbers),复用其计算

### 3.3 规则融合 `fuse(best, runnerup, text) -> chapters`
1. `best` 规则切主体
2. 对每个**离群超长块**(段长 > 均值 + 2σ 或 > 50k 字,复用现有 `_post_process_chapters` 阈值)递归套 `runnerup`(及其它候选)找漏切子章
3. **编号对账**:若主体序号出现跳号(如 …187, 189…),在缺口对应的文本范围内强制用次优/派生规则补扫;补不到则标 `gap_flag` 进报告(不静默)
4. renumber + dedup(复用现有 `_post_process_chapters`)

---

## 4. 三道过滤

### 4.a 广告/乱码清洗 `clean_corpus`(port sanitize.ts → Python)
- 把 `/Users/felix/program/Stellatrix/books/server/utils/sanitize.ts` 的 `AD_LINE_TESTS` / `INLINE_CLEANERS` / `sanitizeCorpusText` **逐条 port 成 Python**,落 `rpg/ingest/sanitize.py`,**取代** `chapter_splitter._strip_pirate_promo`(只有 8 条正则,弱)
- 处理:盗版站推广行、求票/打赏行内插、URL/QQ 群、乱码区块(连续非常用字/replacement char 密度高的行)、全角/半角与 BOM 归一(保留现有 `clean_text` 的编码归一)
- 输出 `cleaning_report`: 删除行数 + 类别计数(ad/garble/promo),进总报告

### 4.b 作者非正文标注 `filter_non_content(chapters) -> chapters`(抓 21.5% 污染)
对每章双信号判定 `is_author_note: bool`(不删,标注 + 默认从提取排除):
- **标题模式**:命中 `卷末/卷首/小结/感言/请假/通知/上架/月票/加更/作者的话/[完]` 等(可配 `AUTHOR_NOTE_TITLE_PATTERNS`)
- **结构密度**:正文极短(< 300 字)+ 对白比例≈0 + 第一人称元叙述密度高("我"+"明天"+"更新"/"大家"邻近)
- 二者其一强命中或两者弱命中 → `is_author_note=True`,`exclude_from_extraction=True`,进报告类别 `author_note`
- **保留原文**(玩家若想看也能看),只是不喂提取/不当剧情章

### 4.c 怪标题 → 内容描述符 `annotate_weird_titles(chapters)`
作者玩梗标题("804说好的爆发推迟了（75）")对提取/时间线无信息且可能误导:
- **判定**:标题与正文首 ~500 字的语义相似度低(BGE-M3 余弦 < 阈值)**或**标题命中"梗模式"(纯口语/含吐槽词/与序号无关名词)。嵌入不可用时降级:纯启发式(标题无实体名 + 含口语词)
- **动作**:`title_confidence: float`;低可信 → 生成 `content_descriptor`(正文首句/首个事件的 8-15 字摘要,A.0 用规则抽,Phase A 提取后回填更准);**原标题只用于显示**,下游提取/时间线一律用 `content_descriptor`
- 这直接接住根因里"时间线只剩章节标题"——标题不可信时不让它进时间线

---

## 5. 质量报告 `build_split_report_v2`(扩现有 `_build_split_report`)
在现有报告字段(mode/confidence/chapter_count/size_cv/problem_category/reasons…)基础上**新增**:
```python
{
  "rule_chosen": {"id", "kind", "score"}, "rule_runnerup": {...},
  "score_breakdown": {seq_continuity, size_uniformity, count_sanity, marker_consistency, coverage},
  "cleaning": {"removed_lines", "by_category": {ad, garble, promo}},
  "gaps": [{"after_chapter", "expected_index", "recovered": bool}],   # 编号对账漏切
  "author_notes": [{"chapter_index", "title", "reason"}],             # 21.5% 污染清单
  "weird_titles": [{"chapter_index", "title", "title_confidence", "content_descriptor"}],
  "needs_review": bool,   # 任何 gap 未恢复 / 大量 author_note / 大量低可信标题 → True,路由 Phase E 复核
}
```
**不静默删**:author_note / weird_title / gap 全部上报,复核 UI(Phase E)可逐条确认/翻案。

---

## 6. 落地改动清单(文件级)
- **新建** `rpg/ingest/sanitize.py` — port sanitize.ts(§4.a)
- **新建** `rpg/ingest/adaptive_split.py` — `build_candidate_rules` / `structural_score` / `fuse`(§3)
- **新建** `rpg/ingest/filters.py` — `filter_non_content` / `annotate_weird_titles`(§4.b/c)
- **改** `rpg/chapter_splitter.py` — `_split_chapters_internal` 调新管线;**删** `REMULINA_*` 常量 + `_should_use_remulina_special` + `_split_remulina_novel` + `_extract_remulina_volume_meta`;`_strip_pirate_promo` → 委托 `ingest.sanitize`;`_build_split_report` → v2
- **改** `rpg/platform_app/script_import.py` — `import_script` 4b/4c 接新管线;把 report v2 存进 `scripts.import_report`(已有 jsonb 列)
- **改** DB:`script_chapters` 加列 `is_author_note bool`、`exclude_from_extraction bool`、`title_confidence real`、`content_descriptor text`(走 `platform_app/db/migrations.py` 新版本号)

## 7. 测试
- 黄金集:在二战书 + 另取 2-3 本结构迥异网文(纯数字章名/卷-章/英文)跑,断言验收标准(§1)
- 单元:`structural_score` 对人工构造的"漏切/粘连/杂牌标题"样本打分排序正确
- 回归:换书不退化(无 REMULINA 后二战书切分质量不降)
