# Phase E — 可视化图编辑器(god-mode)

> 实施级设计。提取产出**草稿图** → 人类在画布上精修 → 精修后的图 = GM 消费的规范 KB。
> **决策4:图数据模型是核心(已在 Phase B 落地),画布 UI 不阻塞 MVP。** 分两步交付:
> **E.1 只读复核表(MVP,跟 Phase B 一起)** → **E.2 交互画布(fast-follow)**。
>
> 现有锚点:前端已 Vite/ESM/React(`frontend/src/`),平台有 `scripts.import_report`(jsonb)、
> `worldbook_entries`/`character_cards`/`kb_canon_*`/`script_worldline_*`(Phase B 产出)。

---

## 1. E.1 — 只读复核表(MVP,先打通 QA,不画图)
目标:Phase B 提取一出,人就能**审**提取质量 + 接住 A.0 的质量报告 flag,无需等画布。
- 页面 `ScriptReview`(剧本详情下新 tab),四个表格视图,纯读 + 行内"接受/标错/编辑摘要":
  1. **实体表**:`kb_canon_entities`(name/type/aliases/summary/first_revealed_chapter/importance)
  2. **时间线表**:`script_timeline_anchors`(phase/纪元/章节范围)
  3. **世界线节点表**:`script_worldline_nodes`(线/节点/弧/must_preserve)
  4. **摄入质量表**:A.0 `import_report.{author_notes, weird_titles, gaps, needs_review}`——逐条确认/翻案
- 行内动作走简单 PATCH 端点(改 summary / 合并别名 / 标 author_note 翻案);**所有编辑是 god-mode 覆盖规范层**
- 价值:幻觉修复(Phase A/B)能**立刻被人验证**,不必等前端画布

## 2. E.2 — 交互画布(fast-follow)
- 节点 = 实体/事件/世界线节点;边 = 关系/世界线顺序/分叉。库:React Flow(轻、成熟、可控)或 Cytoscape.js(大图性能)
- god 操作:确认/合并实体(拖一个到另一个上)、标/移规范世界线锚点、画世界线分叉(从节点拉出 `branch:*` 线)、修关系、删噪声节点、把 A.0 flag 的怪标题/作者非正文逐个裁决
- 保存 = 写回 Phase B 规范层表(`kb_canon_*` / `script_worldline_*` / `worldbook_entries`),版本化(规范层是 per-script 钉死,编辑 = 发新规范版本)

## 3. 后端契约(E.1 + E.2 共用)
- `GET /api/scripts/{id}/graph` → `{entities[], events[], relationships[], worldlines[], nodes[], review_flags}`(读 Phase B 表 + import_report)
- `PATCH /api/scripts/{id}/canon` → god 编辑(合并/改/标),走鉴权(仅 owner/admin),写规范层 + 审计
- 编辑触发**下游重算**:改实体别名 → 重跑该实体的嵌入;改世界线节点 → 重算 steering 索引

## 4. 落地改动清单
- **新建** 前端 `frontend/src/pages/script-review.jsx`(E.1 表)、后续 `script-graph.jsx`(E.2 画布)
- **新建** 后端 `rpg/platform_app/api/script_graph.py`(GET graph / PATCH canon)
- **复用**:Phase B 表 + `import_report` + 现有鉴权/审计
- **依赖**:Phase B 完成(有规范层数据);E.1 可与 Phase B 并行收尾,E.2 排后

## 5. 验收
- E.1:二战书提取后,审表能定位并翻案 A.0 flag 的污染章 / 改错误实体摘要,改动落库
- E.2:拖合并两个同人别名 → 重算嵌入 → GM `lookup_entity` 取到合并后实体;画一条 `branch:*` 世界线 → steering 能识别
