"""RP harness 基准 — judge 增强型 case loader。

在 cases.load_save_cases() 的基础上,为每个 case 追加裁判层所需的额外字段:
  anchor_summary          — 已发生/变体锚点的摘要(不泄露未来)
  must_preserve           — 相关锚点的 must_preserve 列表
  current_chapter         — 当前章节号(来自 state_snapshot.progress.chapter)
  chapter_event_snippet   — chapter_facts.events(主要忠实度真值来源)
  current_chapter_range   — [current_chapter, current_chapter + buffer](剧透控制用)

DB 表与列名已从 platform_app/db/init.py + migrations.py 确认:

save_anchor_states:
  id, save_id, anchor_key, source_kind, source_chapter, source_event_index,
  source_phase_index, script_id, summary, phase_label, must_preserve(jsonb),
  may_vary(jsonb), importance, is_fatal, status, variant_description,
  occurred_at_turn, drift_score, metadata, created_at, updated_at

chapter_facts(PostgreSQL):
  id, book_id, script_id, document_id, chapter_id, chapter, title, viewpoint,
  summary, story_phase, story_time_label, scene_count, token_estimate,
  characters(jsonb), locations(jsonb), factions(jsonb), concepts(jsonb),
  items(jsonb), relationships(jsonb), events(jsonb), confidence,
  metadata, created_at, updated_at

注意:chapter_facts.events 是 jsonb 数组,每项格式由 chapter_fact_indexer 写入,
通常为 {"event": "...", "importance": "high"|"medium"|"low", ...}。
"""
from __future__ import annotations

import json
import re
from typing import Any

from bench.cases import load_save_cases

# 剧透控制缓冲:在当前章节基础上再往后多少章算安全范围
_SPOILER_BUFFER = 2

# prior 轮数上限(judge 层只需要最近 4 轮,比 cases.py 的 8 轮更严)
_PRIOR_LIMIT = 4

# 邮件地址脱敏
_EMAIL_RE = re.compile(r'[\w.+\-]+@[\w.\-]+')


def _scrub_email(s: str) -> str:
    return _EMAIL_RE.sub('[email]', s)


def _load_anchor_data(db, save_id: int) -> list[dict]:
    """拉已发生/变体锚点(不含 pending,不泄露未来)。"""
    rows = db.execute(
        """
        select summary, must_preserve, source_chapter, occurred_at_turn
        from save_anchor_states
        where save_id = %s and status in ('occurred', 'variant')
        order by occurred_at_turn asc nulls last
        """,
        (save_id,),
    ).fetchall()
    result = []
    for r in rows:
        mp = r.get("must_preserve") or []
        if isinstance(mp, str):
            try:
                mp = json.loads(mp)
            except Exception:
                mp = []
        result.append({
            "summary": (r.get("summary") or "").strip(),
            "must_preserve": mp if isinstance(mp, list) else [],
            "source_chapter": r.get("source_chapter"),
            "occurred_at_turn": r.get("occurred_at_turn"),
        })
    return result


def _load_chapter_events(db, script_id: int | None, chapter: int | None) -> str:
    """从 chapter_facts.events(jsonb) 拉当前章节的事件列表,拼成文本。

    chapter_facts 是主要忠实度真值来源;save_anchor_states.occurred 是辅助。
    返回空字符串表示未找到。
    """
    if not script_id or chapter is None:
        return ""
    row = db.execute(
        "select events from chapter_facts where script_id=%s and chapter=%s",
        (script_id, chapter),
    ).fetchone()
    if not row:
        return ""
    evts = row.get("events") or []
    if isinstance(evts, str):
        try:
            evts = json.loads(evts)
        except Exception:
            return ""
    if not isinstance(evts, list):
        return ""
    lines = []
    for evt in evts[:30]:
        if isinstance(evt, dict):
            text = (evt.get("event") or "").strip()
            imp = evt.get("importance", "")
            if text:
                lines.append(f"[{imp}] {text}" if imp else text)
        elif isinstance(evt, str) and evt.strip():
            lines.append(evt.strip())
    return "\n".join(lines)


def _extract_chapter(snap: dict | None) -> int | None:
    """从 state_snapshot 读当前章节号(与 cases.py 同路径)。"""
    if not snap or not isinstance(snap, dict):
        return None
    prog = snap.get("progress") or {}
    if isinstance(prog, dict):
        ch = prog.get("chapter")
        if isinstance(ch, (int, float)):
            return int(ch)
    return None


def load_judge_cases(db, save_id: int, max_cases: int = 50) -> list[dict]:
    """在 load_save_cases 基础上追加 judge 所需字段,返回 judge-ready case 列表。

    额外字段:
      anchor_summary          str  — 已发生锚点摘要(换行拼接,限 600 chars)
      must_preserve           list — 相关锚点 must_preserve 合并(去重)
      current_chapter         int|None
      chapter_event_snippet   str  — chapter_facts.events 文本(主路径)
      current_chapter_range   list[int] — [ch, ch+buffer]

    隐私处理:
      player_input 中的邮件地址已脱敏;prior 裁到最近 4 轮。
    """
    base_cases = load_save_cases(db, save_id)
    if not base_cases:
        return []
    base_cases = base_cases[:max_cases]

    script_id: int | None = base_cases[0].get("script_id") if base_cases else None

    # 一次性拉已发生锚点(所有 case 共用同一个存档的锚点集合)
    anchor_data = _load_anchor_data(db, save_id)

    # 拼 anchor_summary(只用 summary 字段,不含 variant_description)
    anchor_summary_text = "\n".join(
        a["summary"] for a in anchor_data if a.get("summary")
    )[:800]

    # 合并所有已发生锚点的 must_preserve(去重保序)
    all_mp: list[str] = []
    seen_mp: set[str] = set()
    for a in anchor_data:
        for item in (a.get("must_preserve") or []):
            s = str(item).strip()
            if s and s not in seen_mp:
                seen_mp.add(s)
                all_mp.append(s)

    # 提前拉 state_snapshot(需要 chapter),从 cases 里拿不到,要自己查 commit
    # cases.py 从 branch_commits.state_snapshot 读 history;chapter 也在同一 snap 里
    srow = db.execute(
        "select active_commit_id from game_saves where id=%s", (save_id,)
    ).fetchone()
    snap_chapter: int | None = None
    if srow and srow.get("active_commit_id"):
        crow = db.execute(
            "select state_snapshot from branch_commits where id=%s and save_id=%s",
            (srow["active_commit_id"], save_id),
        ).fetchone()
        if crow:
            snap = crow.get("state_snapshot")
            if isinstance(snap, str):
                try:
                    snap = json.loads(snap)
                except Exception:
                    snap = None
            snap_chapter = _extract_chapter(snap)

    # chapter_event_snippet(主路径):从 chapter_facts 拉当前章节事件
    chapter_event_snippet = _load_chapter_events(db, script_id, snap_chapter)

    # current_chapter_range
    if snap_chapter is not None:
        chapter_range = [snap_chapter, snap_chapter + _SPOILER_BUFFER]
    else:
        chapter_range = []

    enriched: list[dict] = []
    for case in base_cases:
        c = dict(case)

        # 隐私:player_input 邮件脱敏
        if c.get("player_input"):
            c["player_input"] = _scrub_email(c["player_input"])

        # prior 裁到最近 4 轮
        prior = c.get("prior") or []
        c["prior"] = prior[-_PRIOR_LIMIT:] if len(prior) > _PRIOR_LIMIT else prior

        # 追加 judge 字段
        c["anchor_summary"] = anchor_summary_text
        c["must_preserve"] = all_mp
        c["current_chapter"] = snap_chapter
        c["chapter_event_snippet"] = chapter_event_snippet
        c["current_chapter_range"] = chapter_range

        enriched.append(c)

    return enriched
