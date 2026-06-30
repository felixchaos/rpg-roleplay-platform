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


def _anchor_pace(user_id: int | None = None) -> bool:
    """Q 锚点限速/同章可见(每用户特性 anchor_pace,默认开)。开时:进度窗口含当前章
    (chapter_min=last_sat,不再 +1),让同章其余未发生锚点留在窗口里能被标记/被引导。"""
    from core.feature_flags import feature_enabled
    return feature_enabled("anchor_pace", user_id)


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
        # Q 根因修复:同名实体「首次引入/首次登场」去重。提取层会把同一实体(如 D20)拆成
        # item + concept(D20_concept/D20系统/...),在不同章各生成一个「首次引入」事件 → 玩家在
        # ch1 碰 D20 却被判到达 ch11 的 D20 锚点 → floor 棘轮跳章。按 chapter 升序只留最早一个首次锚点。
        import re as _re
        def _intro_name(s: str):
            m = _re.search(r"[「『]([^」』]+)[」』]", s)        # item「D20」/ concept「苍白之血」
            if m:
                return m.group(1).strip()
            m = _re.match(r"^场景(.+?)首次", s)                 # 场景秋叶原避难所首次出现
            if m:
                return m.group(1).strip()
            m = _re.match(r"^(.+?)[（(]", s)                    # 爱丽丝(character)首次登场
            return m.group(1).strip() if m else None
        seen_intro_names: set[str] = set()
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
                # 同名实体首次引入/登场/出现去重:只留 chapter 升序里最早那个,跳掉后续重复
                # (治 D20 在 ch1 item + ch11 concept 各生成首次锚点导致的跳章误判;含场景「首次出现」,
                # 与 anchor_reconcile._INTRO_MARKERS 口径一致,避免同一场景多条首次锚点)。
                if ("首次引入" in summary) or ("首次登场" in summary) or ("首次出现" in summary):
                    _nm = _intro_name(summary)
                    if _nm:
                        if _nm in seen_intro_names:
                            continue
                        seen_intro_names.add(_nm)
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
      · 优先用 _canon_importance (kb_canon_entities 反向回填带的 LLM 抽过分,
        在那个量纲里 importance>=2 已是有意义实体,放大 ×8 映射到 0-100)
      · 否则 chapter_facts.events[].importance 字段 (high=70, medium=50)
      · participants 数量 (× 2)
      · locations 数量
      · concepts 数量
      · _CRITICAL_KEYWORDS 命中 (+15 each)
      · _FATAL_KEYWORDS 命中 (+10 each)
    """
    canon_imp = event.get("_canon_importance")
    if isinstance(canon_imp, (int, float)) and canon_imp > 0:
        # kb_canon_entities.importance 量纲: 顶 ~40+, 中坚 ~10, 末梢 ~3
        # ×8 让 imp=5 (卡切尔级关键 NPC) → 40 base, imp=10 (中坚) → 80
        base = min(80, max(40, int(canon_imp) * 8))
    else:
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


def get_progress_window(save_id: int, world_time_label: str | None = None,
                        script_id: int | None = None, *,
                        window_size: int = 50) -> dict[str, Any]:
    """算"游戏进度章节窗口"— 用来限制 list_pending_for_phase 不要全局返回。

    优先级链:
      1. save_anchor_states 里最大 occurred/variant 章节 + 1 → window=[max+1, max+window_size]
         (玩家已推进到原著第 N 章,接下来塞 N+1..N+50 的待发生锚点)
      2. world_time_label != None → 查 script_timeline_anchors story_time_label 匹配的
         chapter range,用 chapter_min 当起点。/set 时间时这条生效。
      3. 都没有 → [1, 30] 剧本开头

    返回 {chapter_min, chapter_max, source: "satisfied"|"label"|"fallback",
          last_satisfied_chapter: int|None}
    """
    init_db()
    sid = int(save_id)
    with connect() as db:
        # 每用户 anchor_pace:无 user 上下文,从 save 反查 owner 判定(单一连接复用)。
        from core.feature_flags import feature_enabled_for_save
        _pace = feature_enabled_for_save("anchor_pace", sid, db)
        # 1. 已处理(occurred / variant / superseded)的最大章节
        #    BUG 修复:原只算 occurred/variant,漏了 superseded。玩家早章绕过某锚点
        #    (mark_anchor_superseded)但此后未满足任何锚点时,last_sat 为 None → 进度窗口
        #    永停在书本开头,GM 反复注入已绕过的开头锚点、剧情推进停滞。绕过也是"这一章
        #    已处理过"的进度信号,纳入计算。
        #    Q 再修(pace on):楼层优先只认【真实到达】(occurred/variant)。superseded 含
        #    save_phase_manager 按 phase_label【粗粒度自动绕过】—— 远未来锚点(ch42)与早章共享
        #    phase_label 被一起 superseded 时,若计入楼层会把楼层/窗口拽到 ch42 = 跳章。故 pace 下
        #    仅当【无任何真实到达】时才回退 superseded(保留上面「绕过全部早章不卡开头」的兜底)。
        if _pace:
            r_reached = db.execute(
                "select max(source_chapter) as max_ch from save_anchor_states "
                "where save_id = %s and status in ('occurred', 'variant')",
                (sid,),
            ).fetchone()
            reached = int(r_reached["max_ch"]) if r_reached and r_reached.get("max_ch") is not None else None
            if reached is not None:
                last_sat = reached
            else:
                r_sup = db.execute(
                    "select max(source_chapter) as max_ch from save_anchor_states "
                    "where save_id = %s and status = 'superseded'",
                    (sid,),
                ).fetchone()
                last_sat = int(r_sup["max_ch"]) if r_sup and r_sup.get("max_ch") is not None else None
        else:
            row = db.execute(
                "select max(source_chapter) as max_ch from save_anchor_states "
                "where save_id = %s and status in ('occurred', 'variant', 'superseded')",
                (sid,),
            ).fetchone()
            last_sat = int(row["max_ch"]) if row and row.get("max_ch") is not None else None
        if last_sat:
            # 根因修复(多锚点同章):last_sat+1 把当前章其余【未发生】锚点挤出窗口 →
            # 永不被判定器标记(更新速度对不上)、永不作为 steering 注入(剧情不按锚点走)。
            # pace on 时 chapter_min=last_sat(含当前章);status='pending' 过滤天然排除已发生的同章锚点。
            chapter_min = last_sat if _pace else last_sat + 1
            return {
                "chapter_min": chapter_min,
                "chapter_max": last_sat + window_size,
                "source": "satisfied",
                "last_satisfied_chapter": last_sat,
            }
        # 1.5 出生点 / 已推进进度(尚无 occurred 锚点时):读 worldline.progress_chapter。
        #     玩家入场选 ch50 出生点 → _build_initial_snapshot 写 progress_chapter=50;此时
        #     last_sat=None,但 pending 锚点窗口 / NPC 抽取 / 贴原著正文 fallback 必须从 ch50 起,
        #     否则仍走下方 fallback[1,30] 注入序章(#62/#63/#66/#67)。progress_chapter 是确定性
        #     数值(出生点显式选择或 advance_progress 由 occurred 锚点推进),比 world_time_label
        #     易错字符串匹配可靠,故优先于 label 步。
        r_prog = db.execute(
            "select (worldline->>'progress_chapter')::int as pc "
            "from game_sessions where save_id = %s",
            (sid,),
        ).fetchone()
        _pc = int(r_prog["pc"]) if r_prog and r_prog.get("pc") is not None else 0
        if _pc > 1:
            return {
                "chapter_min": _pc,
                "chapter_max": _pc + window_size,
                "source": "progress_chapter",
                "last_satisfied_chapter": None,
            }
        # 2. world.time label 匹配 anchor 表
        if world_time_label and script_id:
            row = db.execute(
                "select min(chapter_min) as ch_min, max(chapter_max) as ch_max "
                "from script_timeline_anchors "
                "where script_id = %s and story_time_label = %s",
                (int(script_id), world_time_label.strip()),
            ).fetchone()
            if row and row.get("ch_min") is not None:
                ch_min = int(row["ch_min"])
                return {
                    "chapter_min": ch_min,
                    "chapter_max": ch_min + window_size,
                    "source": "label",
                    "last_satisfied_chapter": None,
                }
    # 3. fallback 剧本开头
    return {
        "chapter_min": 1,
        "chapter_max": 30,
        "source": "fallback",
        "last_satisfied_chapter": None,
    }


def list_pending_for_phase(
    save_id: int,
    phase_label: str | None = None,
    *,
    limit: int = 5,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    order_by_chapter: bool = False,
) -> list[dict[str, Any]]:
    """查待发生的锚点。phase_label 给定时按 phase 过滤; chapter window 给定时按章节范围过滤。

    order_by_chapter=True 时按 source_chapter asc 排(剧情往前走,推荐用于 retrieve_context
    的进度窗口注入)。默认 False = importance desc 先(供 GM 工具按重要度查)。
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
    order_clause = (
        "order by source_chapter asc, importance desc"
        if order_by_chapter
        else "order by importance desc, source_chapter asc"
    )
    sql = f"""
        select id, anchor_key, source_chapter, source_phase_index,
               summary, phase_label, must_preserve, may_vary,
               importance, is_fatal, metadata
        from save_anchor_states
        where {' and '.join(where)}
        {order_clause}
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
