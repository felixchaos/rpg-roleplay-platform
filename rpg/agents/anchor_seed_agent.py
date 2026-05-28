"""
anchor_seed_agent.py — task 136: 世界线收束机制 · 锚点 seed 子代理

设计动机
========
原著的关键事件 (chapter_facts.events) 是"世界线锚点"。锚点必须发生,但发生
的方式可以变。本文件负责: 当一个新存档建立时, 从该剧本的 chapter_facts 抽
出所有重要事件, 拍平到 save_anchor_states 表 (status='pending'), 供 GM 在
后续每一回合查询并主动触发。

公开 API
========
    seed_anchors_for_save(save_id) -> dict
        新存档创建时自动调一次。返回 {ok, seeded, by_phase, fatal_count, ...}.

    reseed_anchors_for_save(save_id, *, keep_satisfied=True) -> dict
        强制重 seed。keep_satisfied=True 时, 已经 occurred/variant 的锚点保留。

    classify_event_fatal(event_text) -> bool
        启发式判断: 这个事件是否"死神来了"模式 (重大死亡 / 失踪 / 战败)。

设计权衡: 为什么是 deterministic 而不是 LLM?
============================================
800 章 × 平均 5 events/章 = 4000 锚点候选。每条都过 LLM 是不必要的开销。
本文件用纯启发式抽取:
  · importance = "high" / "medium" → 直接映射 importance_score 70 / 50
  · 包含 EVENT_KEYWORDS 中的"致命词"→ is_fatal=true
  · must_preserve / may_vary 暂不细分 (LLM 增强留 task 136i 异步任务)

GM 看到的锚点信息是: summary + participants + locations + importance + is_fatal,
这些已经足够让 GM 设计场景把剧情往锚点上引。
"""
from __future__ import annotations

import time
from typing import Any

from psycopg.types.json import Jsonb

from platform_app.db import connect, init_db

# ────────────────────────────────────────────────────────────
#  常量
# ────────────────────────────────────────────────────────────

# "死神来了"模式触发词: 包含这些词的事件被标 is_fatal=true
# 玩家任何阻止尝试都会以替代方式发生。
_FATAL_KEYWORDS = [
    "死亡", "战死", "牺牲", "阵亡", "身亡", "暴毙", "灭门", "灭族",
    "失守", "陷落", "覆灭", "毁灭", "炸毁",
    "失踪", "下落不明",
    "暴露", "败露",
    "投降", "归降",
    "判决", "处决", "枪决", "处刑",
]

# 高重要性额外加权词
_CRITICAL_KEYWORDS = [
    "宣战", "停战", "和约", "登基", "继位", "退位",
    "确认", "公开", "公布",
    "出嫁", "成婚", "联姻",
    "诞生", "出生",
    "驾崩", "薨", "崩",
]

# importance map: chapter_facts.events[].importance → 0-100
_IMPORTANCE_MAP = {
    "high": 70,
    "medium": 50,
    "low": 30,
}


# ────────────────────────────────────────────────────────────
#  公共 API
# ────────────────────────────────────────────────────────────


def seed_anchors_for_save(save_id: int, *, force: bool = False) -> dict[str, Any]:
    """从该 save 关联剧本的 chapter_facts 抽锚点, 写入 save_anchor_states。

    幂等: 已存在的 anchor_key (save_id, anchor_key) 默认不覆盖, 除非 force=True。
    返回 {ok, seeded, by_phase, fatal_count, elapsed_ms, script_id}.
    """
    t0 = time.time()
    init_db()
    if not save_id:
        return {"ok": False, "reason": "no save_id"}

    sid = int(save_id)
    with connect() as db:
        save_row = db.execute(
            "select id, script_id from game_saves where id = %s", (sid,)
        ).fetchone()
        if not save_row:
            return {"ok": False, "reason": f"save {sid} not found"}
        script_id = int(save_row["script_id"])
        if not script_id:
            return {"ok": False, "reason": f"save {sid} 没有关联 script_id"}

        # 拉所有 chapter_facts (按 chapter 升序)
        facts = db.execute(
            """
            select chapter, title, story_phase, story_time_label,
                   events, characters, locations, summary
            from chapter_facts
            where script_id = %s
            order by chapter asc
            """,
            (script_id,),
        ).fetchall()
        if not facts:
            return {
                "ok": True, "seeded": 0, "by_phase": {}, "fatal_count": 0,
                "reason": "no chapter_facts (剧本可能还没拆完书)",
                "script_id": script_id,
            }

        # 已有的 anchor_key 集合 (幂等用)
        existing_keys = set()
        if not force:
            for r in db.execute(
                "select anchor_key from save_anchor_states where save_id = %s",
                (sid,),
            ).fetchall():
                existing_keys.add(r["anchor_key"])

        seeded = 0
        by_phase: dict[str, int] = {}
        fatal_count = 0
        for fact in facts:
            events_raw = fact.get("events") or []
            if not isinstance(events_raw, list):
                continue
            chapter = int(fact["chapter"])
            phase_label = fact.get("story_phase") or ""
            for idx, ev in enumerate(events_raw):
                if not isinstance(ev, dict):
                    continue
                anchor_key = f"chapter:{chapter}:event:{idx}"
                if anchor_key in existing_keys:
                    continue
                summary = str(ev.get("event") or "").strip()
                if not summary:
                    continue
                # 过滤太短的
                if len(summary) < 6:
                    continue
                importance = _compute_importance(ev, summary)
                # 过滤太低的 (减少噪音)
                if importance < 40:
                    continue
                is_fatal = classify_event_fatal(summary)
                participants = ev.get("participants") or []
                locations = ev.get("locations") or []
                concepts = ev.get("concepts") or []
                # may_vary 默认是 ["地点", "时机", "旁观者"]
                # must_preserve 默认是事件主语 + 关键词
                must_preserve = _derive_must_preserve(summary, participants)
                may_vary = ["地点", "触发时机", "旁观者"]
                db.execute(
                    """
                    insert into save_anchor_states (
                      save_id, anchor_key, source_kind, source_chapter,
                      source_event_index, script_id, summary, phase_label,
                      must_preserve, may_vary, importance, is_fatal,
                      status, metadata
                    ) values (%s, %s, 'chapter', %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, 'pending', %s)
                    on conflict (save_id, anchor_key) do nothing
                    """,
                    (
                        sid, anchor_key, chapter, idx, script_id,
                        summary[:300], phase_label[:120],
                        Jsonb(must_preserve), Jsonb(may_vary),
                        importance, is_fatal,
                        Jsonb({
                            "participants": participants[:8],
                            "locations": locations[:4],
                            "concepts": concepts[:5],
                            "seed_source": "deterministic",
                        }),
                    ),
                )
                seeded += 1
                if is_fatal:
                    fatal_count += 1
                by_phase[phase_label] = by_phase.get(phase_label, 0) + 1
        return {
            "ok": True,
            "save_id": sid,
            "script_id": script_id,
            "seeded": seeded,
            "by_phase": by_phase,
            "fatal_count": fatal_count,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }


def reseed_anchors_for_save(save_id: int, *, keep_satisfied: bool = True) -> dict[str, Any]:
    """强制重 seed。keep_satisfied=True 时, 已 occurred/variant 的锚点保留。"""
    t0 = time.time()
    init_db()
    sid = int(save_id)
    with connect() as db:
        if keep_satisfied:
            db.execute(
                """
                delete from save_anchor_states
                where save_id = %s
                  and status not in ('occurred', 'variant')
                """,
                (sid,),
            )
        else:
            db.execute(
                "delete from save_anchor_states where save_id = %s", (sid,)
            )
    res = seed_anchors_for_save(sid, force=True)
    res["reseeded"] = True
    res["keep_satisfied"] = keep_satisfied
    res["total_elapsed_ms"] = int((time.time() - t0) * 1000)
    return res


# ────────────────────────────────────────────────────────────
#  启发式分类
# ────────────────────────────────────────────────────────────


def classify_event_fatal(event_text: str) -> bool:
    """是否是"死神来了"模式锚点 — 包含致命词。"""
    if not event_text:
        return False
    for kw in _FATAL_KEYWORDS:
        if kw in event_text:
            return True
    return False


def _compute_importance(event: dict[str, Any], summary: str) -> int:
    """importance 0-100 综合得分:
      · chapter_facts.events[].importance 字段 (high=70, medium=50)
      · participants 数量 (× 2)
      · locations 数量
      · concepts 数量
      · _CRITICAL_KEYWORDS 命中 (+15 each)
      · _FATAL_KEYWORDS 命中 (+10 each)
    """
    base = _IMPORTANCE_MAP.get(str(event.get("importance") or "").lower(), 40)
    participants = event.get("participants") or []
    locations = event.get("locations") or []
    concepts = event.get("concepts") or []
    bonus = (
        min(len(participants), 5) * 2
        + min(len(locations), 3)
        + min(len(concepts), 3)
    )
    for kw in _CRITICAL_KEYWORDS:
        if kw in summary:
            bonus += 15
            break  # 单条只加一次
    for kw in _FATAL_KEYWORDS:
        if kw in summary:
            bonus += 10
            break
    return min(100, max(0, base + bonus))


def _derive_must_preserve(summary: str, participants: list[Any]) -> list[str]:
    """从事件文本 + 参与者推出 must_preserve 字段。
    保守策略: 主要参与者 + 命中的 critical/fatal 关键词。
    """
    items: list[str] = []
    # 主要参与者 (前 3 人)
    for p in (participants or [])[:3]:
        if isinstance(p, str) and p:
            items.append(f"{p} 参与")
        elif isinstance(p, dict):
            name = str(p.get("name") or "").strip()
            if name:
                items.append(f"{name} 参与")
    # 命中的关键词
    for kw in _CRITICAL_KEYWORDS:
        if kw in summary:
            items.append(kw)
            break
    for kw in _FATAL_KEYWORDS:
        if kw in summary:
            items.append(f"{kw} 这一结果")
            break
    return items[:5]


# ────────────────────────────────────────────────────────────
#  查询辅助 (给 GM 工具用)
# ────────────────────────────────────────────────────────────


def list_pending_for_phase(
    save_id: int,
    phase_label: str | None = None,
    *,
    limit: int = 5,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
) -> list[dict[str, Any]]:
    """查待发生的锚点。phase_label 给定时按 phase 过滤; chapter window 给定时按章节范围过滤。
    按 importance desc + chapter asc 排序。
    """
    init_db()
    sid = int(save_id)
    where = ["save_id = %s", "status = 'pending'"]
    params: list[Any] = [sid]
    if phase_label:
        where.append("phase_label = %s")
        params.append(phase_label)
    if chapter_min is not None:
        where.append("source_chapter >= %s")
        params.append(int(chapter_min))
    if chapter_max is not None:
        where.append("source_chapter <= %s")
        params.append(int(chapter_max))
    sql = f"""
        select id, anchor_key, source_chapter, source_phase_index,
               summary, phase_label, must_preserve, may_vary,
               importance, is_fatal, metadata
        from save_anchor_states
        where {' and '.join(where)}
        order by importance desc, source_chapter asc
        limit %s
    """
    params.append(max(1, int(limit)))
    with connect() as db:
        rows = db.execute(sql, tuple(params)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "anchor_key": r["anchor_key"],
            "chapter": r["source_chapter"],
            "phase_label": r["phase_label"],
            "summary": r["summary"],
            "must_preserve": r["must_preserve"] or [],
            "may_vary": r["may_vary"] or [],
            "importance": r["importance"],
            "is_fatal": r["is_fatal"],
            "metadata": r["metadata"] or {},
        })
    return out


def drift_by_phase(save_id: int) -> list[dict[str, Any]]:
    """task 136g: 按 phase_label 聚合 drift score, 供 UI 时间线展示。

    返回 [{phase_label, total, pending, occurred, variant, superseded,
            fatal_pending, avg_drift, convergence_pressure}, ...]
    按 chapter_min asc 排序。
    convergence_pressure: 0.0 (低) - 1.0 (高), 启发式:
       fatal_pending 多 + drift 高 + pending 多 → 越高
    """
    init_db()
    sid = int(save_id)
    with connect() as db:
        rows = db.execute(
            """
            select
              phase_label,
              min(source_chapter) as ch_min,
              count(*) as total,
              sum(case when status = 'pending'    then 1 else 0 end) as pending,
              sum(case when status = 'occurred'   then 1 else 0 end) as occurred,
              sum(case when status = 'variant'    then 1 else 0 end) as variant,
              sum(case when status = 'superseded' then 1 else 0 end) as superseded,
              sum(case when status = 'pending' and is_fatal then 1 else 0 end) as fatal_pending,
              coalesce(avg(drift_score), 0)::numeric(3,2) as avg_drift
            from save_anchor_states
            where save_id = %s
            group by phase_label
            order by min(source_chapter) asc
            """,
            (sid,),
        ).fetchall() or []
    out = []
    for r in rows:
        total = int(r["total"] or 0)
        pending = int(r["pending"] or 0)
        fatal_pending = int(r["fatal_pending"] or 0)
        avg_drift = float(r["avg_drift"] or 0)
        # convergence_pressure 启发式
        pressure = 0.0
        if total > 0:
            pressure = min(1.0, (
                (pending / total) * 0.4
                + avg_drift * 0.3
                + min(fatal_pending / 3.0, 1.0) * 0.3
            ))
        out.append({
            "phase_label": r["phase_label"] or "",
            "chapter_min": int(r["ch_min"] or 0),
            "total": total,
            "pending": pending,
            "occurred": int(r["occurred"] or 0),
            "variant": int(r["variant"] or 0),
            "superseded": int(r["superseded"] or 0),
            "fatal_pending": fatal_pending,
            "avg_drift": avg_drift,
            "convergence_pressure": round(pressure, 3),
        })
    return out


def summarize_save_anchor_state(save_id: int) -> dict[str, Any]:
    """整体状态: pending/occurred/variant/superseded 各多少 + drift 平均。"""
    init_db()
    sid = int(save_id)
    with connect() as db:
        rows = db.execute(
            """
            select status, count(*) as n,
                   coalesce(avg(drift_score), 0)::numeric(3,2) as avg_drift,
                   sum(case when is_fatal then 1 else 0 end) as fatal_n
            from save_anchor_states
            where save_id = %s
            group by status
            """,
            (sid,),
        ).fetchall()
    out: dict[str, Any] = {
        "save_id": sid,
        "pending": 0, "occurred": 0, "variant": 0, "superseded": 0,
        "fatal_pending": 0, "avg_drift": 0.0, "total": 0,
    }
    total = 0
    weighted = 0.0
    for r in rows:
        status = r["status"]
        n = int(r["n"])
        out[status] = n
        total += n
        weighted += float(r["avg_drift"] or 0) * n
        if status == "pending":
            out["fatal_pending"] = int(r["fatal_n"] or 0)
    out["total"] = total
    out["avg_drift"] = round(weighted / total, 3) if total else 0.0
    return out
