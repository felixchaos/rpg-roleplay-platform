"""
save_phase_manager.py — task 107C

Phase boundary detection + open/close helpers for save_phase_digests.

Public API:
  get_active_phase(save_id)         -> dict | None
  detect_phase_boundary(save_id, state, gm_op_payload=None) -> bool
  open_new_phase(save_id, turn_index, phase_label, story_time_label) -> dict
  close_phase(save_id, phase_index) -> None

Design notes:
- All DB ops are synchronous (called from the turn persist hook).
- upsert_timeline_anchor is also exposed here so chat_pipeline only needs one import.
- PHASE_TURN_THRESHOLD = 30 (configurable via env RPG_PHASE_TURN_THRESHOLD).
"""
from __future__ import annotations

import json
import os
from typing import Any

PHASE_TURN_THRESHOLD = int(os.environ.get("RPG_PHASE_TURN_THRESHOLD", "30"))


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
        from platform_app.db import connect, init_db
        from psycopg.types.json import Jsonb

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
        print(f"[save_phase_manager] upsert_timeline_anchor failed: {exc}")


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
        print(f"[save_phase_manager] get_active_phase failed: {exc}")
        return None


def _next_phase_index(save_id: int) -> int:
    """Return max(phase_index)+1 for this save, or 0 if none exist."""
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            row = db.execute(
                "select coalesce(max(phase_index), -1) as mx from save_phase_digests where save_id = %s",
                (save_id,),
            ).fetchone()
        return int((row or {}).get("mx", -1)) + 1
    except Exception:
        return 0


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
    """
    try:
        from platform_app.db import connect, init_db
        from psycopg.types.json import Jsonb

        init_db()
        with connect() as db:
            # Close existing open phase at turn_index - 1
            db.execute(
                """
                update save_phase_digests
                   set status   = 'closed',
                       turn_end = %s,
                       updated_at = now()
                 where save_id = %s and status = 'open'
                """,
                (max(0, turn_index - 1), save_id),
            )
            new_index = _next_phase_index(save_id)
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
        # task 107D 集成: 新 phase 一旦 open 成功, 老 phase 已被 close ->
        # fire-and-forget 触发 LLM 摘要老 phase (不阻塞玩家 chat)
        if new_index > 0:
            _fire_and_forget_compact(save_id, new_index - 1)
        return dict(row) if row else {"phase_index": new_index, "save_id": save_id}
    except Exception as exc:
        print(f"[save_phase_manager] open_new_phase failed: {exc}")
        return {}


def _fire_and_forget_compact(save_id: int, phase_index: int) -> None:
    """task 107D/107E 集成: 异步调 phase_digest_agent.compact_phase 摘要这个 phase.

    fire-and-forget: 不等待结果, 不影响玩家 chat 流。
    失败时只 print, 下次 worker 扫 needs_rebuild 时会重试。
    """
    import threading

    def _worker() -> None:
        try:
            from phase_digest_agent import compact_phase
            result = compact_phase(save_id, phase_index)
            err = (result or {}).get("error")
            if err:
                print(f"[phase_digest async] save {save_id} phase {phase_index} LLM error: {err}")
                # 标记 needs_rebuild 让 worker 后续重试
                try:
                    from platform_app.db import connect, init_db
                    from psycopg.types.json import Jsonb
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
            print(f"[phase_digest async] phase_digest_agent not yet available, "
                  f"save {save_id} phase {phase_index} will be backfilled later")
        except Exception as exc:
            print(f"[phase_digest async] save {save_id} phase {phase_index}: "
                  f"{type(exc).__name__}: {exc}")

    threading.Thread(target=_worker, daemon=True, name=f"compact-{save_id}-{phase_index}").start()


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
        print(f"[save_phase_manager] close_phase failed: {exc}")


# ────────────────────────────────────────────────────────────
# Ensure phase 0 exists (called on first turn for a save)
# ────────────────────────────────────────────────────────────


def ensure_initial_phase(save_id: int, turn_index: int, phase_label: str = "", story_time_label: str = "") -> None:
    """Open phase 0 if no phase exists yet for this save."""
    active = get_active_phase(save_id)
    if active is not None:
        return
    try:
        from platform_app.db import connect, init_db
        from psycopg.types.json import Jsonb

        init_db()
        with connect() as db:
            existing = db.execute(
                "select 1 from save_phase_digests where save_id = %s limit 1",
                (save_id,),
            ).fetchone()
            if existing:
                return
            db.execute(
                """
                insert into save_phase_digests
                    (save_id, phase_index, turn_start, turn_end,
                     story_time_label, phase_label,
                     summary, key_events, key_npcs, key_locations, key_decisions,
                     emotion_arc, status, generated_by, metadata)
                values (%s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (save_id, phase_index) do nothing
                """,
                (
                    save_id,
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
                "update game_saves set active_phase_index = 0 where id = %s",
                (save_id,),
            )
    except Exception as exc:
        print(f"[save_phase_manager] ensure_initial_phase failed: {exc}")


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
        print(f"[save_phase_manager] update_phase_turn_end failed: {exc}")


__all__ = [
    "upsert_timeline_anchor",
    "get_active_phase",
    "detect_phase_boundary",
    "open_new_phase",
    "close_phase",
    "ensure_initial_phase",
    "update_phase_turn_end",
    "PHASE_TURN_THRESHOLD",
]
