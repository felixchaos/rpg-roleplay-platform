"""kb.reveal — 时间感知知识库:揭示锚点 DAG(P1 回填)+ 前沿可见集(P4)。

设计:docs/design/O_temporal_kb_unification.md。
P1(本文件):确定性 ETL,把剧本的 chapter_facts.events 物化成 reveal_anchors(剧本级揭示锚点 DAG)。
- anchor_key 与 save_anchor_states 完全对齐(`chapter:{n}:event:{idx}`,见 agents/anchor_seed_agent),
  这样某存档把锚点标 occurred 时,save_reveal_frontier 能按同 key 对上 reveal_anchors。
- requires 按「章→事件」顺序把合格锚点连成单条主线链(worldline_key='main')→ 线性叙事骨架;
  到达某锚点 ⇒ 其传递闭包(=之前所有锚点)进可见集(P4 用)。
- 复用 anchor_seed_agent 的 importance/fatal/must_preserve 逻辑,保证与 save 级 seeding 同口径(同一批锚点)。
- 幂等:on conflict 只刷新 source='novel' 行,绝不动 editor/gm 新建的锚点。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from platform_app.db import connect, init_db

_MIN_SUMMARY_LEN = 6
_MIN_IMPORTANCE = 40
_DEFAULT_MAY_VARY = ["地点", "触发时机", "旁观者"]


def _collect_anchor_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 chapter_facts(按 chapter 升序)抽合格事件,返回按(章,事件序)有序的锚点行。纯函数,便于单测。"""
    from agents.anchor_seed_agent import (
        _compute_importance,
        _derive_must_preserve,
        classify_event_fatal,
    )
    rows: list[dict[str, Any]] = []
    for fact in facts:
        events_raw = fact.get("events") or []
        if not isinstance(events_raw, list):
            continue
        chapter = int(fact["chapter"])
        phase = (fact.get("story_phase") or "")[:120]
        stl = (fact.get("story_time_label") or "")[:200]
        for idx, ev in enumerate(events_raw):
            if not isinstance(ev, dict):
                continue
            summary = str(ev.get("event") or "").strip()
            if len(summary) < _MIN_SUMMARY_LEN:
                continue
            importance = _compute_importance(ev, summary)
            if importance < _MIN_IMPORTANCE:
                continue
            rows.append({
                "anchor_key": f"chapter:{chapter}:event:{idx}",
                "chapter": chapter,
                "story_phase": phase,
                "story_time_label": stl,
                "summary": summary[:300],
                "importance": importance,
                "is_fatal": classify_event_fatal(summary),
                "must_preserve": _derive_must_preserve(summary, ev.get("participants") or []),
            })
    return rows


def backfill_reveal_anchors(script_id: int) -> dict[str, Any]:
    """P1:回填 reveal_anchors(剧本级揭示锚点 DAG)。幂等。返回 {ok, script_id, anchors}。"""
    init_db()
    sid = int(script_id)
    with connect() as db:
        facts = db.execute(
            "select chapter, story_phase, story_time_label, events from chapter_facts "
            "where script_id = %s order by chapter asc",
            (sid,),
        ).fetchall()
        rows = _collect_anchor_rows([dict(f) for f in facts])
        prev_key: str | None = None
        seeded = 0
        for a in rows:
            requires = [prev_key] if prev_key else []
            db.execute(
                """
                insert into reveal_anchors (
                    script_id, anchor_key, chapter_min, chapter_max,
                    story_phase, story_time_label, requires, worldline_key, kind,
                    summary, must_preserve, may_vary, importance, is_fatal, source
                ) values (%s, %s, %s, %s, %s, %s, %s, 'main', 'beat',
                          %s, %s, %s, %s, %s, 'novel')
                on conflict (script_id, anchor_key) do update set
                    chapter_min = excluded.chapter_min,
                    chapter_max = excluded.chapter_max,
                    story_phase = excluded.story_phase,
                    story_time_label = excluded.story_time_label,
                    requires = excluded.requires,
                    summary = excluded.summary,
                    must_preserve = excluded.must_preserve,
                    importance = excluded.importance,
                    is_fatal = excluded.is_fatal,
                    updated_at = now()
                where reveal_anchors.source = 'novel'
                """,
                (
                    sid, a["anchor_key"], a["chapter"], a["chapter"],
                    a["story_phase"], a["story_time_label"],
                    Jsonb(requires), a["summary"],
                    Jsonb(a["must_preserve"]), Jsonb(_DEFAULT_MAY_VARY),
                    a["importance"], a["is_fatal"],
                ),
            )
            prev_key = a["anchor_key"]
            seeded += 1
    return {"ok": True, "script_id": sid, "anchors": seeded}


# ── P4:存档前沿(reached-set / DAG 可见集) ──────────────────────────────────
# 全部确定性、无 LLM。可见集 = 前沿锚点 + 其 requires 传递闭包(DAG 祖先)。
# 这套替代标量 progress_chapter 做剧透天花板;派生 progress_chapter = 可见锚点的 MAX(chapter_max)
# (确定性,绝不会像旧猜章器那样冲到玩家没到的章 → 根治「跳章」)。

_CLOSURE_CTE = """
with recursive closure(anchor_key, requires) as (
    select ra.anchor_key, ra.requires from reveal_anchors ra
      where ra.script_id = %(scr)s
        and ra.anchor_key in (select anchor_key from save_reveal_frontier where save_id = %(sid)s)
  union
    select pred.anchor_key, pred.requires
      from closure c
      cross join lateral jsonb_array_elements_text(c.requires) as req(key)
      join reveal_anchors pred on pred.script_id = %(scr)s and pred.anchor_key = req.key
)
select anchor_key from closure
"""


def _script_id_for_save(db, save_id: int) -> int | None:
    r = db.execute("select script_id from game_saves where id=%s", (int(save_id),)).fetchone()
    return int(r["script_id"]) if r and r.get("script_id") is not None else None


def recompute_visible_set(db, save_id: int, script_id: int) -> int:
    """重算 save_visible_anchors = 前沿锚点的 requires 传递闭包。返回可见锚点数。"""
    sid, scr = int(save_id), int(script_id)
    db.execute("delete from save_visible_anchors where save_id=%s", (sid,))
    rows = db.execute(_CLOSURE_CTE, {"sid": sid, "scr": scr}).fetchall()
    for r in rows:
        db.execute(
            "insert into save_visible_anchors(save_id, anchor_key) values (%s,%s) "
            "on conflict (save_id, anchor_key) do nothing",
            (sid, r["anchor_key"]),
        )
    return len(rows)


def seed_frontier(save_id: int) -> dict[str, Any]:
    """P4:从 save_anchor_states(occurred/variant)确定性回填 save_reveal_frontier + 重算可见集。
    幂等。anchor_key 与 reveal_anchors 对齐(同 chapter:{n}:event:{idx} 体系)。"""
    init_db()
    sid = int(save_id)
    with connect() as db:
        scr = _script_id_for_save(db, sid)
        if not scr:
            return {"ok": False, "reason": f"save {sid} 无 script_id"}
        reached = db.execute(
            "select anchor_key, source_chapter, occurred_at_turn, drift_score, status "
            "from save_anchor_states where save_id=%s and status in ('occurred','variant')",
            (sid,),
        ).fetchall()
        for r in reached:
            db.execute(
                """
                insert into save_reveal_frontier (save_id, script_id, anchor_key, reached_at_turn,
                                                  reached_via, drift_score, worldline_key)
                values (%s, %s, %s, %s, %s, %s, 'main')
                on conflict (save_id, anchor_key) do nothing
                """,
                (sid, scr, r["anchor_key"], r.get("occurred_at_turn"),
                 'seed', r.get("drift_score") or 0),
            )
        visible = recompute_visible_set(db, sid, scr)
    return {"ok": True, "save_id": sid, "script_id": scr,
            "frontier_seeded": len(reached), "visible": visible}


def mark_anchor_reached(save_id: int, anchor_key: str, *, turn: int | None = None,
                        via: str = "gm", drift: float = 0.0) -> dict[str, Any]:
    """P4:把一条锚点加入前沿(GM 声明到达)+ 增量并入可见集。前沿只增不减(回退走 rewind)。"""
    init_db()
    sid = int(save_id)
    key = (anchor_key or "").strip()
    if not key:
        return {"ok": False, "reason": "anchor_key 为空"}
    with connect() as db:
        scr = _script_id_for_save(db, sid)
        if not scr:
            return {"ok": False, "reason": f"save {sid} 无 script_id"}
        db.execute(
            """
            insert into save_reveal_frontier (save_id, script_id, anchor_key, reached_at_turn,
                                              reached_via, drift_score, worldline_key)
            values (%s, %s, %s, %s, %s, %s, coalesce(
                (select worldline_key from reveal_anchors where script_id=%s and anchor_key=%s), 'main'))
            on conflict (save_id, anchor_key) do nothing
            """,
            (sid, scr, key, turn, via, drift, scr, key),
        )
        visible = recompute_visible_set(db, sid, scr)
    return {"ok": True, "save_id": sid, "anchor_key": key, "visible": visible}


def derived_progress_chapter(save_id: int, *, db=None) -> int:
    """派生只读进度 = 可见锚点的 MAX(chapter_max)。确定性,绝不超过玩家真实到达的章(根治跳章)。
    无可见锚点(新档/未回填)→ 返回 1(保守开局)。"""
    def _q(_db):
        r = _db.execute(
            "select coalesce(max(ra.chapter_max), 0) as c from reveal_anchors ra "
            "join save_visible_anchors sva on sva.anchor_key = ra.anchor_key "
            "where ra.script_id = (select script_id from game_saves where id=%s) and sva.save_id=%s",
            (int(save_id), int(save_id)),
        ).fetchone()
        return max(1, int((r or {}).get("c") or 0))
    if db is not None:
        return _q(db)
    init_db()
    with connect() as db2:
        return _q(db2)


def backfill_entity_reveal_anchors(script_id: int) -> dict[str, Any]:
    """P4 前置:把三张实体表的 reveal_anchor_key 从 first_revealed_chapter 映射到「该章的揭示锚点」,
    使新前沿门控与旧「first_revealed_chapter <= progress」等价(shadow-compare 才能零 diff)。

    映射规则(确定性):first_revealed_chapter = N > 0 → 取 main 线 reveal_anchors 里:
      优先 chapter_min >= N 的最近一条(到达它=进度>=N,等价旧语义);若无(N 超出所有锚点)→ 取
      chapter_min 最大的一条(末章可见,保守不剧透)。N <= 0 → 留 NULL(=未知/恒可见,等价旧 0<=progress)。
    幂等。返回各表映射条数。
    """
    init_db()
    sid = int(script_id)
    # 每实体取一条锚点:prefer chapter_min>=N(升序最近),否则 chapter_min 最大者。
    pick = (
        "select ra.anchor_key from reveal_anchors ra "
        "where ra.script_id=%(scr)s and ra.worldline_key='main' "
        "order by (ra.chapter_min >= %(n)s) desc, "
        "         case when ra.chapter_min >= %(n)s then ra.chapter_min else -ra.chapter_min end asc, "
        "         ra.anchor_key asc limit 1"
    )
    out: dict[str, int] = {}
    specs = [
        ("character_cards", "card_type='npc' and "),
        ("kb_canon_entities", ""),
        ("worldbook_entries", ""),
    ]
    with connect() as db:
        # 没有 reveal_anchors(P1 没回填)→ 跳过,避免把所有实体钉到 NULL(那会变成恒可见=剧透)
        has = db.execute("select 1 from reveal_anchors where script_id=%s limit 1", (sid,)).fetchone()
        if not has:
            return {"ok": False, "script_id": sid, "reason": "reveal_anchors 未回填,先跑 P1 backfill_reveal_anchors"}
        for table, extra in specs:
            rows = db.execute(
                f"select id, coalesce(first_revealed_chapter,0) as n from {table} "
                f"where script_id=%s and {extra}coalesce(first_revealed_chapter,0) > 0",
                (sid,),
            ).fetchall()
            n_mapped = 0
            for r in rows:
                pr = db.execute(pick, {"scr": sid, "n": int(r["n"])}).fetchone()
                if pr and pr.get("anchor_key"):
                    db.execute(
                        f"update {table} set reveal_anchor_key=%s, reveal_known=true where id=%s",
                        (pr["anchor_key"], r["id"]),
                    )
                    n_mapped += 1
            out[table] = n_mapped
    return {"ok": True, "script_id": sid, "mapped": out, "total": sum(out.values())}


def reveal_clause_v2(save_id: int, mode: str = "none", prefix: str = "") -> tuple[str, list[Any]]:
    """收口剧透门控(替代标量 _reveal_clause)。返回 (SQL 片段, 参数列表)。
    节点可见 ⇔ 无揭示锚点(NULL) 或 其锚点在 save_visible_anchors。partial 再放行 public_knowledge。
    调用方把片段嵌进 WHERE 并按顺序传参。reveal_anchor_key 列名前缀由 prefix 指定(如 'cc.')。"""
    p = prefix or ""
    m = (mode or "none").strip().lower()
    if m == "omniscient":
        return "true", []
    base = (f"({p}reveal_anchor_key is null or {p}reveal_anchor_key in "
            f"(select anchor_key from save_visible_anchors where save_id=%s))")
    if m == "partial":
        return f"({base} or {p}public_knowledge)", [int(save_id)]
    return base, [int(save_id)]
