from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from platform_app.db import connect, expose, init_db, limit_value, page_payload
from platform_app.knowledge._context_runs_repo import (
    _db_insert_turn_messages,
    _db_select_context_runs,
    _db_update_context_run_status,
)
from platform_app.knowledge._utils import _cursor_int, _retrieved_chunks_payload
from platform_app.knowledge.session import ensure_game_session


def record_context_run(
    user_id: int,
    save_id: int,
    state: dict[str, Any],
    user_input: str,
    agent_result: dict[str, Any],
    bundle: dict[str, Any],
    retrieved_context: str,
    *,
    status: str = "done",
    error: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """记录一次上下文召回。status: running / done / stopped / failed。"""
    session = ensure_game_session(user_id, save_id, state)
    debug = bundle.get("debug") or {}
    with connect() as db:
        row = db.execute(
            """
            insert into context_runs(
              session_id, save_id, user_id, turn, user_input, agent_steps,
              curator_plan, layers, active_character_cards, active_worldbook,
              retrieved_chunks, estimated_tokens, cache_plan,
              status, error, duration_ms
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning *
            """,
            (
                session["id"],
                save_id,
                user_id,
                int(state.get("turn") or 0),
                user_input,
                Jsonb(agent_result.get("steps") or []),
                Jsonb(agent_result.get("curator_plan") or {}),
                Jsonb(debug.get("layers") or []),
                Jsonb(debug.get("active_character_cards") or []),
                Jsonb(debug.get("active_worldbook") or []),
                Jsonb(_retrieved_chunks_payload(retrieved_context)),
                int(debug.get("estimated_tokens") or 0),
                Jsonb(debug.get("cache_plan") or {}),
                status,
                error,
                int(duration_ms),
            ),
        ).fetchone()
    return expose(row)  # type: ignore[return-value]


def update_context_run_status(run_id: int, status: str, error: str = "", duration_ms: int | None = None) -> None:
    """更新已存在 context_run 的状态（如打断/失败转写）。"""
    init_db()
    with connect() as db:
        _db_update_context_run_status(db, run_id, status, error, duration_ms)


def record_turn_messages(
    user_id: int,
    save_id: int,
    state: dict[str, Any],
    player_input: str,
    gm_output: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = ensure_game_session(user_id, save_id, state)
    turn = int(state.get("turn") or 0)
    with connect() as db:
        user_msg, gm_msg = _db_insert_turn_messages(db, session["id"], save_id, turn, player_input, gm_output, metadata or {})
    return {"user": expose(user_msg), "assistant": expose(gm_msg)}


def list_context_runs(user_id: int, save_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        rows = _db_select_context_runs(db, save_id, before_id, page_limit)
    return page_payload(rows, page_limit)
