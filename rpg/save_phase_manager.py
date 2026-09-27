"""
save_phase_manager.py — task 107C

Phase boundary detection + open/close helpers for save_phase_digests.

Public API:
  get_active_phase(save_id)         -> dict | None
  detect_phase_boundary(save_id, state, gm_op_payload=None) -> bool
  open_new_phase(save_id, turn_index, phase_label, story_time_label) -> dict
  close_phase(save_id, phase_index) -> None
  ensure_active_phase(save_id, turn_index, ...)  -> None  (每回合调,无 open phase 就补开)

Design notes:
- All DB ops are synchronous (called from the turn persist hook).
- upsert_timeline_anchor is also exposed here so chat_pipeline only needs one import.
- PHASE_TURN_THRESHOLD = 30 (configurable via env RPG_PHASE_TURN_THRESHOLD).
"""
from __future__ import annotations

from typing import Any

from core.config import phase_turn_threshold as _phase_turn_threshold
from core.logging import get_logger

log = get_logger(__name__)

PHASE_TURN_THRESHOLD = _phase_turn_threshold()


# ────────────────────────────────────────────────────────────
# Timeline anchor upsert (107B)
# ────────────────────────────────────────────────────────────


def upsert_timeline_anchor(
    save_id: int,
    turn_index: int,
    story_time_label: str,
    phase_label: str,
    source: str = "gm",
    delta_label: str = "",
    metadata: dict | None = None,
) -> None:
    """Upsert a row in save_timeline_anchors for the given turn.

    ON CONFLICT (save_id, turn_index) DO UPDATE — safe to call repeatedly.
    Silent on failure: must never crash the turn pipeline.
    """
    try:
        from psycopg.types.json import Jsonb

        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            db.execute(
                """
                insert into save_timeline_anchors
                    (save_id, turn_index, story_time_label, phase_label, source,
                     delta_label, metadata)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (save_id, turn_index) do update
                    set story_time_label = excluded.story_time_label,
                        phase_label      = excluded.phase_label,
                        source           = excluded.source,
                        delta_label      = excluded.delta_label,
                        metadata         = excluded.metadata
                """,
                (
                    save_id,
                    turn_index,
                    story_time_label or "",
                    phase_label or "",
                    source,
                    delta_label or "",
                    Jsonb(metadata or {}),
                ),
            )
    except Exception as exc:
        log.warning(f"[save_phase_manager] upsert_timeline_anchor failed: {exc}")


# ────────────────────────────────────────────────────────────
# Phase read helpers
# ────────────────────────────────────────────────────────────


def get_active_phase(save_id: int) -> dict | None:
    """Return the single open (status='open') phase with the highest phase_index, or None."""
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            row = db.execute(
                """
                select * from save_phase_digests
                where save_id = %s and status = 'open'
                order by phase_index desc
                limit 1
                """,
                (save_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.warning(f"[save_phase_manager] get_active_phase failed: {exc}")
        return None


# ────────────────────────────────────────────────────────────
# Phase boundary detection (107C)
# ────────────────────────────────────────────────────────────


def detect_phase_boundary(
    save_id: int,
    state: Any,
    gm_op_payload: dict | None = None,
) -> bool:
    """Return True if any trigger condition is met.

    Trigger conditions (any of):
      a) active phase turn count >= PHASE_TURN_THRESHOLD
      b) gm_op_payload contains {"op": "phase_advance", "label": "..."}
      c) state.world.timeline.pending_jump was just confirmed
      d) state.world.timeline.current_phase differs from active phase label
    """
    active = get_active_phase(save_id)
    if active is None:
        # No phase yet — always open a first phase on next turn, not here.
        return False

    # b) GM explicit op
    if isinstance(gm_op_payload, dict) and gm_op_payload.get("op") == "phase_advance":
        return True

    try:
        world = (state.data.get("world") or {}) if hasattr(state, "data") else {}
        timeline = world.get("timeline") or {}

        # c) time jump confirmed
        pending_jump = timeline.get("pending_jump") or {}
        if isinstance(pending_jump, dict) and pending_jump.get("confirmed"):
            return True

        # d) chapter switch: current_phase in state differs from active phase label
        current_phase_label = (timeline.get("current_phase") or "").strip()
        active_phase_label = (active.get("phase_label") or "").strip()
        if current_phase_label and active_phase_label and current_phase_label != active_phase_label:
            return True
    except Exception:
        pass

    # a) turn threshold
    try:
        turn_start = int(active.get("turn_start") or 0)
        turn_end = int(active.get("turn_end") or turn_start)
        elapsed = max(0, turn_end - turn_start + 1)
        if elapsed >= PHASE_TURN_THRESHOLD:
            return True
    except Exception:
        pass

    return False


# ────────────────────────────────────────────────────────────
# Phase open / close (107C)
# ────────────────────────────────────────────────────────────


def open_new_phase(
    save_id: int,
    turn_index: int,
    phase_label: str = "",
    story_time_label: str = "",
) -> dict:
    """Close current open phase (if any) and insert a new open phase.

    Also updates game_saves.active_phase_index.
    Returns the newly inserted row as a dict.

    当前 open phase 若一个回合都还没收进来(turn_start > turn_index-1),不关它、就地改造
    成新 phase(重设起点与标签),不摘要、不审计 —— 按 turn_index-1 关它会得到倒挂空区间
    [s, s-1]:compact 恒失败、留 needs_rebuild 毒行给 cron 天天重试,
    锚点审计还把整段 story_phase 的 fatal 锚点报成「超期」。来源是分支回退后残留的旧
    open phase(continue_from / activate_node 不修剪 phase,回到开场后新分支 turn 1 标签
    一变,detect_phase_boundary 就触发)、同回合二次开 phase、阈值被配成 ≤1 等。
    本函数是关闭 open phase 的唯一缝,在这里判一次,各来源都兜得住。
    """
    try:
        from psycopg.types.json import Jsonb

        from platform_app.db import connect, init_db

        init_db()
        close_end = max(0, turn_index - 1)
        repurposed = False
        stale_to_digest: list[int] = []
        with connect() as db:
            # 同一连接、同一事务读 open phase 并锁行:与每回合钩子里的 update_phase_turn_end /
            # 并发的 open_new_phase 串行,判定与改写之间不留缝。
            open_rows = db.execute(
                """
                select phase_index, turn_start, turn_end
                  from save_phase_digests
                 where save_id = %s and status = 'open'
                 order by phase_index desc
                   for update
                """,
                (save_id,),
            ).fetchall() or []
            cur = open_rows[0] if open_rows else None
            if cur is not None and int(cur.get("turn_start") or 0) > close_end:
                keep_index = int(cur["phase_index"])
                # 历史异常:同时开着多条 open 行。较小 index 的那些按各自 turn_end 关掉
                # (不改区间),区间正常的才补摘要;倒挂的不摘要(find_pending 也不会挑它)。
                for extra in open_rows[1:]:
                    idx = int(extra["phase_index"])
                    db.execute(
                        "update save_phase_digests set status = 'closed', updated_at = now() "
                        "where save_id = %s and phase_index = %s",
                        (save_id, idx),
                    )
                    if int(extra.get("turn_end") or 0) >= int(extra.get("turn_start") or 0):
                        stale_to_digest.append(idx)
                row = db.execute(
                    """
                    update save_phase_digests
                       set turn_start       = %s,
                           turn_end         = %s,
                           phase_label      = %s,
                           story_time_label = %s,
                           updated_at       = now()
                     where save_id = %s and phase_index = %s
                    returning *
                    """,
                    (turn_index, turn_index, phase_label or "", story_time_label or "",
                     save_id, keep_index),
                ).fetchone()
                db.execute(
                    "update game_saves set active_phase_index = %s where id = %s",
                    (keep_index, save_id),
                )
                new_index = keep_index
                repurposed = True
            else:
                # Close existing open phase at turn_index - 1
                db.execute(
                    """
                    update save_phase_digests
                       set status   = 'closed',
                           turn_end = %s,
                           updated_at = now()
                     where save_id = %s and status = 'open'
                    """,
                    (close_end, save_id),
                )
                # 同一连接算下一个 index(别在持有本连接时另开一条连接去查 max ——
                # PgBouncer 池上是自找死锁;与 ensure_active_phase 同法)。
                mx = db.execute(
                    "select coalesce(max(phase_index), -1) as mx "
                    "from save_phase_digests where save_id = %s",
                    (save_id,),
                ).fetchone()
                new_index = int((mx or {}).get("mx", -1)) + 1
                row = db.execute(
                    """
                    insert into save_phase_digests
                        (save_id, phase_index, turn_start, turn_end,
                         story_time_label, phase_label,
                         summary, key_events, key_npcs, key_locations, key_decisions,
                         emotion_arc, status, generated_by, metadata)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (save_id, phase_index) do update
                        set status           = 'open',
                            turn_start       = excluded.turn_start,
                            turn_end         = excluded.turn_end,
                            story_time_label = excluded.story_time_label,
                            phase_label      = excluded.phase_label,
                            updated_at       = now()
                    returning *
                    """,
                    (
                        save_id,
                        new_index,
                        turn_index,
                        turn_index,
                        story_time_label or "",
                        phase_label or "",
                        "",                # summary — filled by 107D LLM agent
                        Jsonb([]),
                        Jsonb([]),
                        Jsonb([]),
                        Jsonb([]),
                        "",                # emotion_arc
                        "open",
                        "llm",
                        Jsonb({}),
                    ),
                ).fetchone()
                # Update game_saves.active_phase_index
                db.execute(
                    "update game_saves set active_phase_index = %s where id = %s",
                    (new_index, save_id),
                )
        if repurposed:
            log.info(
                f"[save_phase_manager] save {save_id} phase {new_index} 尚未收进任何回合,"
                f"就地改到 turn {turn_index} 起算(不关成空段)"
            )
            for idx in stale_to_digest:
                _fire_and_forget_compact(save_id, idx)
            return dict(row) if row else {"phase_index": new_index, "save_id": save_id}
        # task 107D 集成: 新 phase 一旦 open 成功, 老 phase 已被 close ->
        # fire-and-forget 触发 LLM 摘要老 phase (不阻塞玩家 chat)。
        # 注意:这里按「新 index > 0」触发,而不是按本次 update 实际关了几行 ——
        # /compact 先由 compact_phase(force=True) 把 phase 关掉再调本函数,本次关 0 行,
        # 但老 phase 的锚点审计仍得跑(摘要那头命中 already_closed 短路,不调 LLM)。
        if new_index > 0:
            _fire_and_forget_compact(save_id, new_index - 1)
            # task 136f: 世界线收束 phase boundary audit —
            # 老 phase 的 pending 锚点要么 fatal (留警告) 要么自动 superseded
            _audit_anchors_on_phase_close(save_id, new_index - 1)
        return dict(row) if row else {"phase_index": new_index, "save_id": save_id}
    except Exception as exc:
        log.warning(f"[save_phase_manager] open_new_phase failed: {exc}")
        return {}


def _audit_anchors_on_phase_close(save_id: int, closed_phase_index: int) -> None:
    """task 136f: 老 phase 关闭时 audit 世界线收束锚点。

    规则:
    - 老 phase 的 pending 锚点中, is_fatal=true → 留 pending + 写 audit_log warning
      (下个 phase 的 GM 仍能看到, 但会被强制注意"超期 fatal 锚点")
    - 非 fatal pending → 自动 mark superseded, reason="phase 已结束未触发, 自动绕过"

    不阻塞主流程; 任何异常 print 警告即可。
    """
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            # 取该 phase_index 对应的 phase_label,再按 phase_label 过滤锚点
            phase_row = db.execute(
                "select phase_label, turn_end from save_phase_digests "
                "where save_id = %s and phase_index = %s",
                (save_id, closed_phase_index),
            ).fetchone()
            if not phase_row:
                return
            phase_label = phase_row.get("phase_label") or ""
            turn_end = int(phase_row.get("turn_end") or 0)
            if not phase_label:
                return
            # 该 phase 的 pending 锚点
            rows = db.execute(
                """
                select id, anchor_key, is_fatal, summary, importance
                from save_anchor_states
                where save_id = %s and phase_label = %s and status = 'pending'
                """,
                (save_id, phase_label),
            ).fetchall() or []
            if not rows:
                return
            fatal_pending = [r for r in rows if r.get("is_fatal")]
            non_fatal = [r for r in rows if not r.get("is_fatal")]
            # 非 fatal: 自动 superseded
            # Q 修复(章感知,flag RPG_ANCHOR_PACE):legacy 按 phase_label 一刀切绕过【整个 phase】的
            # pending 锚点 —— 但 phase_label 粒度粗,常把远未来章(实测 ch42)和玩家其实会去/已进入的
            # 当前章(秋叶原)一起绕过 → 退役强制锚点 + superseded 计入楼层把进度拽跳。pace on 时改为
            # 只绕过玩家【确实走过】的章(source_chapter < 已到达 occurred/variant 楼层)的未触发锚点;
            # 未来/当前位置锚点留 pending(玩家可能仍会做),交收束/判定器后续处理。
            from core.feature_flags import feature_enabled_for_save
            pace = feature_enabled_for_save("anchor_pace", save_id, db)
            if non_fatal and pace:
                fr = db.execute(
                    "select max(source_chapter) as m from save_anchor_states "
                    "where save_id = %s and status in ('occurred', 'variant')",
                    (save_id,),
                ).fetchone()
                reached = int(fr["m"]) if fr and fr.get("m") is not None else None
                if reached is None:
                    log.info(f"[anchor_audit] save={save_id} phase={closed_phase_index} "
                             f"pace: 玩家尚无任何到达锚点,phase 关闭不自动绕过(避免误退役)")
                else:
                    res = db.execute(
                        """
                        update save_anchor_states
                        set status = 'superseded',
                            variant_description = %s,
                            drift_score = 1.0,
                            updated_at = now()
                        where save_id = %s
                          and phase_label = %s
                          and status = 'pending'
                          and is_fatal = false
                          and source_chapter < %s
                        returning id
                        """,
                        (
                            f"phase '{phase_label}' 已结束 (turn {turn_end}),玩家已推进过第 {reached} 章,"
                            f"本 phase 中更早未触发的锚点自动绕过",
                            save_id, phase_label, reached,
                        ),
                    ).fetchall()
                    log.info(f"[anchor_audit] save={save_id} phase={closed_phase_index} "
                             f"pace 章感知: 绕过 {len(res)} 个早于第 {reached} 章的 non-fatal 锚点"
                             f"(未来/当前位置锚点保留 pending)")
            elif non_fatal:
                db.execute(
                    """
                    update save_anchor_states
                    set status = 'superseded',
                        variant_description = %s,
                        drift_score = 1.0,
                        updated_at = now()
                    where save_id = %s
                      and phase_label = %s
                      and status = 'pending'
                      and is_fatal = false
                    """,
                    (
                        f"phase '{phase_label}' 已结束 (turn {turn_end}) 未触发, 自动绕过",
                        save_id, phase_label,
                    ),
                )
                log.info(f"[anchor_audit] save={save_id} phase={closed_phase_index} "
                         f"自动 superseded {len(non_fatal)} 个 non-fatal 锚点")
            # fatal: 留 pending,但写到 save_phase_digests.metadata 警告字段
            if fatal_pending:
                from psycopg.types.json import Jsonb
                warning = {
                    "fatal_anchors_overdue": [
                        {"anchor_key": r["anchor_key"], "summary": r["summary"][:120],
                         "importance": r["importance"]}
                        for r in fatal_pending
                    ],
                    "audit_at_phase_close": closed_phase_index,
                }
                db.execute(
                    "update save_phase_digests "
                    "set metadata = coalesce(metadata, '{}'::jsonb) || %s "
                    "where save_id = %s and phase_index = %s",
                    (Jsonb(warning), save_id, closed_phase_index),
                )
                log.warning(f"[anchor_audit] save={save_id} phase={closed_phase_index} "
                            f"WARNING: {len(fatal_pending)} 个 is_fatal 锚点超期未触发, 已记录")
    except Exception as exc:
        log.error(f"[anchor_audit] save={save_id} phase={closed_phase_index} failed: "
                  f"{type(exc).__name__}: {exc}")


def _fire_and_forget_compact(save_id: int, phase_index: int) -> None:
    """task 107D/107E 集成: 异步调 phase_digest_agent.compact_phase 摘要这个 phase.

    fire-and-forget: 不等待结果, 不影响玩家 chat 流。
    失败时只 print, 下次 worker 扫 needs_rebuild 时会重试。
    """
    import threading

    def _worker() -> None:
        try:
            from agents.phase_digest_agent import compact_phase
            user_id = _load_save_user_id(save_id)
            result = compact_phase(save_id, phase_index, user_id=user_id)
            err = (result or {}).get("error")
            if err and (result or {}).get("code") == "empty_range":
                # 这段一个回合都没有(区间倒挂或 commit 已不在):重试也不会成功,compact_phase
                # 已把行标成 digest_empty 终态;别再置 needs_rebuild 让 cron 天天空转。
                log.info(f"[phase_digest async] save {save_id} phase {phase_index} 无可摘要回合,跳过: {err}")
            elif err:
                # 失败未必是模型的锅(取 key、解析、DB 都可能),措辞别写死成 LLM error。
                log.warning(f"[phase_digest async] save {save_id} phase {phase_index} compact 失败: {err}")
                # 标记 needs_rebuild 让 worker 后续重试
                try:
                    from psycopg.types.json import Jsonb

                    from platform_app.db import connect, init_db
                    init_db()
                    with connect() as db:
                        db.execute(
                            "update save_phase_digests set metadata = metadata || %s "
                            "where save_id=%s and phase_index=%s",
                            (Jsonb({"needs_rebuild": True}), save_id, phase_index),
                        )
                except Exception:
                    pass
        except ImportError:
            log.warning(f"[phase_digest async] phase_digest_agent not yet available, "
                        f"save {save_id} phase {phase_index} will be backfilled later")
        except Exception as exc:
            log.error(f"[phase_digest async] save {save_id} phase {phase_index}: "
                      f"{type(exc).__name__}: {exc}")

    threading.Thread(target=_worker, daemon=True, name=f"compact-{save_id}-{phase_index}").start()


def _load_save_user_id(save_id: int) -> int | None:
    """Return the owner for model/key selection in background phase digest jobs."""
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            row = db.execute(
                "select user_id from game_saves where id = %s",
                (save_id,),
            ).fetchone()
        if not row or row.get("user_id") is None:
            return None
        return int(row["user_id"])
    except Exception as exc:
        log.warning(
            f"[phase_digest async] save {save_id} owner lookup failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def close_phase(save_id: int, phase_index: int) -> None:
    """Force-close a phase by setting status='closed'."""
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            db.execute(
                "update save_phase_digests set status = 'closed', updated_at = now() "
                "where save_id = %s and phase_index = %s",
                (save_id, phase_index),
            )
    except Exception as exc:
        log.error(f"[save_phase_manager] close_phase failed: {exc}")


# ────────────────────────────────────────────────────────────
# Ensure an OPEN phase exists (called every turn for a save)
# ────────────────────────────────────────────────────────────


def ensure_active_phase(save_id: int, turn_index: int, phase_label: str = "", story_time_label: str = "") -> None:
    """保证该存档**有一个 open phase**;没有就开下一个 (首回合即 phase 0)。

    原实现叫 ensure_initial_phase,判据是「有没有 phase 行」而不是「有没有 open
    phase」—— 一旦某条路径把 open phase 关掉却没重开(compact_phase(force=True)、
    open_new_phase 关完插入失败、老 /compact 等),这里见到 closed 行就早退、
    detect_phase_boundary 又因无 active phase 恒 False,**该存档自此永久停止折叠
    历史**:save_phase_digests 冻在开局那几个 phase,前情提要/「已发生历史摘要」
    从此永远讲开局剧情(生产 12 个存档处于该状态,其中一个已打到 1000+ 回合、
    前情提要还停在第 1-12 回合)。判据改成「有没有 open phase」= 所有关闭路径
    统一在这条每回合都跑的确定性缝上自愈,不必逐个 closer 打补丁。

    新 phase 从**当前回合**起算:冻结期那段既无 open phase 也无 commit 归属,
    不做追溯压缩(那会把上千回合塞进一次 LLM 摘要),它由 KB/情节召回覆盖。
    """
    active = get_active_phase(save_id)
    if active is not None:
        return
    try:
        from psycopg.types.json import Jsonb

        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            # 同一连接算下一个 index(别在持有本连接时另开一条连接去查 max ——
            # PgBouncer 池上是自找死锁)。
            row = db.execute(
                "select coalesce(max(phase_index), -1) as mx "
                "from save_phase_digests where save_id = %s",
                (save_id,),
            ).fetchone()
            new_index = int((row or {}).get("mx", -1)) + 1
            db.execute(
                """
                insert into save_phase_digests
                    (save_id, phase_index, turn_start, turn_end,
                     story_time_label, phase_label,
                     summary, key_events, key_npcs, key_locations, key_decisions,
                     emotion_arc, status, generated_by, metadata)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (save_id, phase_index) do nothing
                """,
                (
                    save_id,
                    new_index,
                    turn_index,
                    turn_index,
                    story_time_label or "",
                    phase_label or "",
                    "",
                    Jsonb([]),
                    Jsonb([]),
                    Jsonb([]),
                    Jsonb([]),
                    "",
                    "open",
                    "llm",
                    Jsonb({}),
                ),
            )
            db.execute(
                "update game_saves set active_phase_index = %s where id = %s",
                (new_index, save_id),
            )
        if new_index > 0:
            log.info(
                f"[save_phase_manager] save {save_id} 无 open phase(冻结),"
                f"已在 turn {turn_index} 补开 phase {new_index}"
            )
    except Exception as exc:
        log.warning(f"[save_phase_manager] ensure_active_phase failed: {exc}")


# 旧名保留:外部/测试仍可能按老名字 import。
ensure_initial_phase = ensure_active_phase


# ────────────────────────────────────────────────────────────
# Update turn_end of the current open phase
# ────────────────────────────────────────────────────────────


def update_phase_turn_end(save_id: int, turn_index: int) -> None:
    """Extend turn_end of the open phase to turn_index (called every turn)."""
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            db.execute(
                """
                update save_phase_digests
                   set turn_end   = greatest(turn_end, %s),
                       updated_at = now()
                 where save_id = %s and status = 'open'
                """,
                (turn_index, save_id),
            )
    except Exception as exc:
        log.warning(f"[save_phase_manager] update_phase_turn_end failed: {exc}")


__all__ = [
    "upsert_timeline_anchor",
    "get_active_phase",
    "detect_phase_boundary",
    "open_new_phase",
    "close_phase",
    "ensure_active_phase",
    "ensure_initial_phase",  # 旧名别名
    "update_phase_turn_end",
    "PHASE_TURN_THRESHOLD",
]
