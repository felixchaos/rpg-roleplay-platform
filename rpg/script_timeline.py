"""
script_timeline.py — 剧本线性时间线锚点系统。

## 背景

每本剧本导入后,`chapter_facts` 表里每章都有:
  · story_phase     — 剧本宏观阶段标签 ("初期穿越与火星线" / "柏林暗流篇" / ...)
  · story_time_label — 章节级时间标签 ("原著第 X 章附近" / "第一天" / ...)

这些数据形成了"线性时间线",但**没有聚合表**,/set 时间 / GM retrieval 都查不到。

本模块做三件事:
  1. ETL: rebuild_timeline_anchors(script_id) 把 chapter_facts group by 后写到
     script_timeline_anchors 表
  2. Resolve: resolve_timeline_anchor(script_id, label) 模糊匹配用户输入的
     时间标签 → 锚点 dict {chapter_min, chapter_max, story_phase, ...}
  3. Hook: 在 chat 流程 / set_parser / script 导入后自动重建锚点

## /set 流程接入

用户输入 `/set 设置时间为火星·扬陆城内` →
  apply_set_directive → update_time("火星·扬陆城内", source="user_set") →
  state.world.timeline.current_label = "火星·扬陆城内"

然后 chat handler 调:
  anchor = resolve_timeline_anchor(script_id, "火星·扬陆城内")
  # {chapter_min: 1, chapter_max: 255, story_phase: "初期穿越与火星线", confidence: 0.87}
  state.world.timeline.update({
    "anchor_chapter": anchor.chapter_min,
    "chapter_min": anchor.chapter_min,
    "chapter_max": anchor.chapter_max,
    "anchor_phase": anchor.story_phase,
    "anchor_event": anchor.sample_summary[:80],
    "anchor_confidence": anchor.confidence,
  })

之后:
  · context_engine._timeline_layer 把 anchor_phase / chapter_range 给 GM
  · novel retrieval provider 用 chapter_min/max 过滤 BM25 / chunks 召回
"""
from __future__ import annotations

import re
from typing import Any

from platform_app.db import connect, init_db

# ── Phase 1: ETL — 从 chapter_facts 聚合到 script_timeline_anchors ──


def rebuild_timeline_anchors(script_id: int) -> dict[str, Any]:
    """从 chapter_facts 聚合 (script_id, story_phase, story_time_label) →
    (chapter_min, chapter_max, count, sample_title, sample_summary, keywords)。

    返回 {ok, anchors_count, phases, ...}
    """
    init_db()
    if not script_id:
        return {"ok": False, "reason": "no script_id"}
    sid = int(script_id)
    with connect() as db:
        # 检查剧本存在
        sc = db.execute("select id, title from scripts where id = %s", (sid,)).fetchone()
        if not sc:
            return {"ok": False, "reason": f"script {sid} not found"}
        # 删旧锚点
        db.execute("delete from script_timeline_anchors where script_id = %s", (sid,))
        # 聚合
        rows = db.execute(
            """
            select
              coalesce(story_phase, '') as phase,
              coalesce(story_time_label, '') as time_label,
              min(chapter) as ch_min,
              max(chapter) as ch_max,
              count(*) as n,
              (array_agg(title order by chapter))[1] as sample_title,
              (array_agg(summary order by chapter))[1] as sample_summary
            from chapter_facts
            where script_id = %s
            group by story_phase, story_time_label
            order by min(chapter)
            """,
            (sid,),
        ).fetchall()
        if not rows:
            return {"ok": True, "anchors_count": 0, "phases": []}
        anchors_count = 0
        phases_seen: dict[str, int] = {}
        for r in rows:
            phase = r.get("phase") or ""
            time_label = r.get("time_label") or ""
            ch_min = int(r.get("ch_min") or 0)
            ch_max = int(r.get("ch_max") or 0)
            n = int(r.get("n") or 0)
            sample_title = (r.get("sample_title") or "")[:200]
            sample_summary = (r.get("sample_summary") or "")[:500]
            # 关键词:phase 词 + label 词 + sample_title 关键字
            kw_set: set[str] = set()
            for src in (phase, time_label, sample_title):
                for tok in _extract_keywords(src):
                    kw_set.add(tok)
            keywords = sorted(kw_set)[:20]
            # 置信度:n 越大越稳;暂粗算
            confidence = 1.0 if n >= 5 else (0.5 + 0.1 * n)
            db.execute(
                """
                insert into script_timeline_anchors (
                  script_id, story_phase, story_time_label,
                  chapter_min, chapter_max, chapter_count,
                  sample_title, sample_summary, keywords, confidence
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (sid, phase, time_label, ch_min, ch_max, n,
                 sample_title, sample_summary, keywords, confidence),
            )
            anchors_count += 1
            phases_seen[phase] = phases_seen.get(phase, 0) + 1
        return {
            "ok": True,
            "script_id": sid,
            "anchors_count": anchors_count,
            "phases": sorted(phases_seen.keys()),
            "phases_count": len(phases_seen),
        }


def rebuild_all_scripts_timeline_anchors() -> dict[str, Any]:
    """对所有剧本批量重建。用于一次性 backfill / 后台任务。"""
    init_db()
    with connect() as db:
        rows = db.execute(
            "select id from scripts where id in (select distinct script_id from chapter_facts)"
        ).fetchall()
    results = []
    for r in rows:
        results.append(rebuild_timeline_anchors(int(r["id"])))
    return {"ok": True, "count": len(results), "scripts": results}


# ── Phase 2: Resolve — 用户输入 label → 锚点 ────────────────────────


def resolve_timeline_anchor(script_id: int, label: str) -> dict[str, Any] | None:
    """模糊匹配用户输入的时间标签 → 锚点。

    label e.g. "火星·扬陆城内" / "柏林" / "第一天" / "原著第50章"
    Returns: {chapter_min, chapter_max, story_phase, story_time_label,
              chapter_count, sample_summary, confidence, score} 或 None

    Algorithm:
      1. 提取 label 关键词 (除停用词 / 标点)
      2. 查 script_timeline_anchors WHERE script_id 全部锚点
      3. 算每个锚点的 overlap score:
         · keywords ∩ label_tokens (强)
         · story_phase contains label substring (强)
         · story_time_label contains label substring (中)
         · sample_summary contains label substring (弱)
      4. 章节号显式匹配 ("第50章" → ch_min <= 50 <= ch_max)
      5. 返回 top-1
    """
    if not script_id or not label:
        return None
    init_db()
    label = str(label).strip()
    if not label:
        return None
    label_tokens = set(_extract_keywords(label))
    # 显式章节号
    chapter_hint = _extract_chapter_number(label)
    with connect() as db:
        rows = db.execute(
            """
            select id, story_phase, story_time_label, chapter_min, chapter_max,
                   chapter_count, sample_title, sample_summary, keywords, confidence
            from script_timeline_anchors
            where script_id = %s
            """,
            (int(script_id),),
        ).fetchall()
    if not rows:
        return None
    best: dict[str, Any] | None = None
    best_score = 0.0
    for r in rows:
        phase = r.get("story_phase") or ""
        time_label = r.get("story_time_label") or ""
        kws = r.get("keywords") or []
        ch_min = int(r.get("chapter_min") or 0)
        ch_max = int(r.get("chapter_max") or 0)
        sample = r.get("sample_summary") or ""
        score = 0.0
        # ── 全部信号通用化,不依赖任何特定剧本的词汇 ──

        # 1. 章节号显式 hint (用户输入"第 X 章") → 强信号
        if chapter_hint and ch_min <= chapter_hint <= ch_max:
            score += 10.0

        # 2. 关键词 overlap (label token ∩ anchor.keywords)
        #    ETL 阶段对 phase / time_label / sample_title 提取的 2-gram + 完整词
        kw_set = set(kws)
        overlap = kw_set & label_tokens
        score += 2.0 * len(overlap)

        # 3. label 整体子串 vs phase / time_label / sample (强通用匹配)
        #    用户输入 "火星" → 'X' in "初期穿越与火星线" → 命中
        #    用户输入 "Hogwarts" → 'X' in "Hogwarts 一年级" → 命中
        #    用户输入 "建安五年" → 'X' in "建安五年·官渡" → 命中
        #    这是真正通用的 substring 匹配,不需要词典。
        label_norm = label.strip()
        if label_norm:
            # 直接 substring (完整 label 出现在 phase 或 time_label)
            if label_norm in phase:
                score += 6.0  # 强信号:用户输入整段命中 phase
            if label_norm in time_label:
                score += 4.0
            if label_norm in sample:
                score += 1.0

        # 4. label 各 token (n-gram 子串) 命中 phase / time_label
        #    e.g. label="火星·扬陆城内" → tokens=['火星', '扬陆', '城内', ...]
        #    每个 token 在 phase / time_label 子串里 → 加分
        for token in label_tokens:
            if not token or len(token) < 2:
                continue
            if token in phase:
                score += 3.0
            if token in time_label:
                score += 2.0
            if token in sample:
                score += 0.5

        # 5. 锚点置信度加成 (大锚点更稳)
        score += float(r.get("confidence") or 0.5) * 0.5
        if score > best_score:
            best_score = score
            best = {
                "id": int(r["id"]),
                "story_phase": phase,
                "story_time_label": time_label,
                "chapter_min": ch_min,
                "chapter_max": ch_max,
                "chapter_count": int(r.get("chapter_count") or 0),
                "sample_title": r.get("sample_title") or "",
                "sample_summary": sample[:200],
                "confidence": float(r.get("confidence") or 0.5),
                "score": score,
            }
    # 阈值过滤:score < 5.0 视为无匹配 (需要至少 1-2 个强信号:章节号显式 /
    # phase substring / 2+ 个关键词重叠)。过低会误匹配无关 label。
    if best and best_score >= 5.0:
        return best
    return None


def list_timeline_anchors(script_id: int) -> list[dict[str, Any]]:
    """列出某剧本的所有锚点 (用于前端展示 timeline picker)。"""
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select story_phase, story_time_label, chapter_min, chapter_max,
                   chapter_count, sample_title, confidence
            from script_timeline_anchors
            where script_id = %s
            order by chapter_min
            """,
            (int(script_id),),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 关键词提取 helper ─────────────────────────────────────────


_STOP_TOKENS = {
    "的", "了", "在", "是", "我", "你", "他", "她", "它",
    "和", "与", "或", "及", "之", "也", "都", "就",
    "时", "时间", "地点", "·",
    "附近", "篇章", "篇", "章", "原著", "第", "前", "后",
}


def _extract_keywords(text: str) -> list[str]:
    """从中文文本提取关键词。简化:按非汉字分割,过滤停用词。"""
    if not text:
        return []
    # 按非汉字 / 非字母数字切分
    chunks = re.findall(r"[一-鿿\w]+", str(text))
    tokens: list[str] = []
    for chunk in chunks:
        # 中文 chunk: 整段当一个 token + 滑动 2-gram
        if re.match(r"^[一-鿿]+$", chunk):
            if len(chunk) <= 4:
                tokens.append(chunk)
            else:
                # 长 chunk 切 2-gram + 完整保留
                tokens.append(chunk)
                for i in range(len(chunk) - 1):
                    bigram = chunk[i:i + 2]
                    if bigram not in _STOP_TOKENS:
                        tokens.append(bigram)
        else:
            tokens.append(chunk.lower())
    # 去停用词 + 去重 + 长度 >= 2
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if not t or len(t) < 2 or t in _STOP_TOKENS:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _extract_chapter_number(label: str) -> int | None:
    """从 label 抽章节号:'第 50 章' / '第50章' / '50 章' → 50。"""
    m = re.search(r"第?\s*(\d{1,4})\s*章", label)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


__all__ = [
    "rebuild_timeline_anchors",
    "rebuild_all_scripts_timeline_anchors",
    "resolve_timeline_anchor",
    "list_timeline_anchors",
]
