"""
worldbook_agent.py — task 84/86: 世界书子代理。

分层信息架构:
  Layer 0: 原文 (script_chapters / document_chunks FTS)
  Layer 1: ChapterFact (chapter_facts) — 每章一行,含 phase/time/locations/characters/events
  Layer 2: PhaseDigest (phase_digests) — 同 phase 多章聚合,含 chapter range + 关键事件
  Layer 3: WorldTimeline — phase_digests 按时间顺序遍列,作为全局时间轴

入口 API:
  consult(script_id, query, *, current_phase=None, current_time=None,
          jump_to_phase=None, jump_to_chapter=None) -> WorldbookResult

返回包含 confidence: 0=完全没匹配, 1.0=精确命中。
GM 拿到 confidence<0.4 应当走"翻阅未果"兜底文案(question op 让玩家确认),
不应硬编一段未知场景。

这是确定性算法, 不调 LLM(快, 低延迟, 可解释)。后续可以加 LLM 重排。

通用性: 任何剧本只要 chapter_facts + phase_digests + worldbook_entries
都灌好, 这个 agent 就能用。不依赖任何特定书的硬编码。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


@dataclass
class WorldbookResult:
    confidence: float = 0.0  # [0, 1]
    timeline_anchor: dict[str, Any] = field(default_factory=dict)  # {phase, chapter, time_label}
    phase_digest: dict[str, Any] | None = None
    chapter_facts: list[dict[str, Any]] = field(default_factory=list)
    worldbook_entries: list[dict[str, Any]] = field(default_factory=list)
    progress_note: str = ""  # 大幅跳跃时的 progress 说明
    sources: list[str] = field(default_factory=list)  # 拉了哪些层
    elapsed_ms: int = 0

    def to_context_text(self) -> str:
        """打包成 GM context bundle 的一段文本。"""
        parts: list[str] = []
        if self.timeline_anchor:
            a = self.timeline_anchor
            parts.append(
                f"=== 当前时间线锚点 ===\n"
                f"故事 phase: {a.get('phase', '(未匹配)')}\n"
                f"参考章节: 第{a.get('chapter_min', '?')}-{a.get('chapter_max', '?')}章\n"
                f"时间标签: {a.get('time_label', '')}"
            )
        if self.phase_digest:
            pd = self.phase_digest
            parts.append(
                f"=== 阶段摘要 ({pd.get('phase_label', '')}) ===\n"
                f"{pd.get('summary', '')[:1500]}"
            )
        if self.chapter_facts:
            lines = []
            for cf in self.chapter_facts[:5]:
                ev = "; ".join(str(e.get("event", "")) for e in (cf.get("events") or [])[:2])
                lines.append(
                    f"第{cf['chapter']}章《{cf.get('title', '')}》｜{cf.get('story_time_label', '')}\n"
                    f"  摘要: {(cf.get('summary') or '')[:200]}\n"
                    f"  事件: {ev[:160]}"
                )
            parts.append("=== 相关章节事实 ===\n" + "\n\n".join(lines))
        if self.worldbook_entries:
            lines = []
            for wb in self.worldbook_entries[:5]:
                lines.append(f"【{wb.get('title', '')}】\n{(wb.get('content') or '')[:400]}")
            parts.append("=== 世界设定 ===\n" + "\n\n".join(lines))
        if self.progress_note:
            parts.append(f"=== 跳跃进度说明 ===\n{self.progress_note}")
        return "\n\n".join(parts)


def consult(
    script_id: int,
    query: str,
    *,
    current_phase: str = "",
    current_time: str = "",
    jump_to_phase: str = "",
    jump_to_chapter: int | None = None,
) -> WorldbookResult:
    """主入口。

    参数:
      script_id      — 当前剧本 id
      query          — 玩家原话 + GM 内部检索关键词
      current_phase  — state.world.timeline.current_phase 当前故事阶段
      current_time   — state.world.time
      jump_to_phase  — 用户大幅时间跳跃的目标 phase
      jump_to_chapter — 或目标章节号

    返回 WorldbookResult, confidence 在 [0, 1]。
    """
    from platform_app.db import connect as _connect

    t0 = time.time()
    result = WorldbookResult()
    if not script_id:
        result.confidence = 0.0
        return result

    try:
        with _connect() as db:
            # 1) 找 timeline anchor: 优先按 jump_to 直接定位, 否则按 current_phase + query 匹配
            anchor = _resolve_anchor(
                db, script_id, query=query,
                current_phase=current_phase,
                current_time=current_time,
                jump_to_phase=jump_to_phase,
                jump_to_chapter=jump_to_chapter,
            )
            if anchor:
                result.timeline_anchor = anchor
                result.sources.append("phase_digests")

            # 2) 拉 PhaseDigest 对应行
            if anchor and anchor.get("phase"):
                row = db.execute(
                    """select phase_label, chapter_min, chapter_max, summary,
                              key_events, key_locations, key_characters,
                              story_time_label_start, story_time_label_end, chapter_count
                       from phase_digests where script_id=%s and phase_label=%s""",
                    (script_id, anchor["phase"]),
                ).fetchone()
                if row:
                    result.phase_digest = dict(row)

            # 3) 拉 相关 ChapterFact
            cmin = anchor.get("chapter_min") if anchor else None
            cmax = anchor.get("chapter_max") if anchor else None
            cf_rows = db.execute(
                """select chapter, title, story_time_label, summary, events
                   from chapter_facts where script_id=%s
                     and (%s::int is null or chapter >= %s)
                     and (%s::int is null or chapter <= %s)
                   order by chapter limit 5""",
                (script_id, cmin, cmin, cmax, cmax),
            ).fetchall()
            result.chapter_facts = [dict(r) for r in cf_rows] if cf_rows else []
            if result.chapter_facts:
                result.sources.append("chapter_facts")

            # 4) Worldbook entries: 高 priority + key 命中
            scan_blob = " ".join([query or "", current_phase or "", current_time or "",
                                  (anchor.get("time_label") if anchor else "") or ""])
            wb_rows = db.execute(
                """select title, content, keys, priority from worldbook_entries
                   where script_id=%s and enabled=true
                   order by priority desc, id asc limit 30""",
                (script_id,),
            ).fetchall()
            picks = []
            for r in wb_rows or []:
                pri = int(r.get("priority") or 50)
                keys = r.get("keys") or []
                hit = pri >= 90 or any(isinstance(k, str) and k and k in scan_blob for k in keys)
                if hit:
                    picks.append(dict(r))
                if len(picks) >= 5:
                    break
            result.worldbook_entries = picks
            if picks:
                result.sources.append("worldbook_entries")

            # 5) 跳跃 progress note
            if jump_to_phase and current_phase and jump_to_phase != current_phase:
                # 拿 jump_to phase 的关键事件给 GM 当 progress
                jp = db.execute(
                    "select phase_label, summary, key_events, chapter_count "
                    "from phase_digests where script_id=%s and phase_label=%s",
                    (script_id, jump_to_phase),
                ).fetchone()
                if jp:
                    events_brief = "; ".join(
                        str(e.get("event", ""))[:80]
                        for e in (jp.get("key_events") or [])[:6]
                        if isinstance(e, dict)
                    )
                    result.progress_note = (
                        f"玩家从 [{current_phase}] 跳到 [{jump_to_phase}]。"
                        f"目标阶段共 {jp.get('chapter_count', '?')} 章,主要进度: {events_brief[:600]}"
                    )

    except Exception as exc:
        result.confidence = 0.0
        result.elapsed_ms = int((time.time() - t0) * 1000)
        result.progress_note = f"(子代理检索异常: {type(exc).__name__})"
        return result

    # 6) 算 confidence
    score = 0.0
    if result.timeline_anchor:
        score += 0.4
    if result.phase_digest:
        score += 0.3
    if result.chapter_facts:
        score += 0.2
    if result.worldbook_entries:
        score += 0.1
    result.confidence = min(1.0, score)
    result.elapsed_ms = int((time.time() - t0) * 1000)
    return result


def _resolve_anchor(
    db, script_id: int, *, query: str, current_phase: str, current_time: str,
    jump_to_phase: str, jump_to_chapter: int | None,
) -> dict[str, Any] | None:
    """决定 timeline anchor。优先级:
    1) jump_to_chapter — 找 chapter 落在哪个 phase
    2) jump_to_phase  — 直接用 phase_label
    3) current_phase  — 用 state 已知 phase
    4) query 关键词 — phase_label 或 time_label 包含 query
    5) fallback: 第一个 phase (chapter 1 起)
    """
    # 1) 章节号
    if jump_to_chapter:
        r = db.execute(
            "select phase_label, chapter_min, chapter_max, story_time_label_start "
            "from phase_digests where script_id=%s and %s between chapter_min and chapter_max "
            "order by chapter_max - chapter_min asc limit 1",
            (script_id, jump_to_chapter),
        ).fetchone()
        if r:
            return _anchor_from_row(r)
    # 2) jump phase
    if jump_to_phase:
        r = db.execute(
            "select phase_label, chapter_min, chapter_max, story_time_label_start "
            "from phase_digests where script_id=%s and phase_label=%s",
            (script_id, jump_to_phase),
        ).fetchone()
        if r:
            return _anchor_from_row(r)
    # 3) current phase
    if current_phase:
        r = db.execute(
            "select phase_label, chapter_min, chapter_max, story_time_label_start "
            "from phase_digests where script_id=%s and phase_label=%s",
            (script_id, current_phase),
        ).fetchone()
        if r:
            return _anchor_from_row(r)
    # 4) query 关键词
    if query:
        terms = [t for t in re.split(r"[\s,，。.\-]+", query) if len(t) >= 2]
        for term in terms[:6]:
            r = db.execute(
                "select phase_label, chapter_min, chapter_max, story_time_label_start "
                "from phase_digests where script_id=%s "
                "  and (phase_label ilike %s or story_time_label_start ilike %s "
                "       or story_time_label_end ilike %s) "
                "order by chapter_min limit 1",
                (script_id, f"%{term}%", f"%{term}%", f"%{term}%"),
            ).fetchone()
            if r:
                return _anchor_from_row(r)
    # 5) fallback: 第一个 phase
    r = db.execute(
        "select phase_label, chapter_min, chapter_max, story_time_label_start "
        "from phase_digests where script_id=%s order by chapter_min asc limit 1",
        (script_id,),
    ).fetchone()
    if r:
        return _anchor_from_row(r)
    return None


def _anchor_from_row(r: dict) -> dict[str, Any]:
    return {
        "phase": r["phase_label"],
        "chapter_min": r["chapter_min"],
        "chapter_max": r["chapter_max"],
        "time_label": r.get("story_time_label_start") or "",
    }


__all__ = ["consult", "WorldbookResult"]
