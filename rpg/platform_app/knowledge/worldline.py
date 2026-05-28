from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from platform_app.db import connect, expose, init_db
from platform_app.knowledge._utils import _clean_text
from platform_app.knowledge._worldline_repo import _db_select_worldline_variables
from platform_app.knowledge.session import _state_from_save, ensure_game_session


def set_worldline_variable(user_id: int, save_id: int, key: str, value: str, source: str = "user") -> dict[str, Any]:
    key = _clean_text(key)
    value = _clean_text(value)
    if not key or not value:
        raise ValueError("变量名和变量值不能为空")
    session = ensure_game_session(user_id, save_id, _state_from_save(user_id, save_id))
    with connect() as db:
        row = db.execute(
            """
            insert into worldline_variables(session_id, key, value, locked, source, metadata)
            values (%s, %s, %s, true, %s, %s)
            on conflict(session_id, key) do update set
              value = excluded.value,
              locked = excluded.locked,
              source = excluded.source,
              metadata = excluded.metadata,
              updated_at = now()
            returning *
            """,
            (session["id"], key, value, source, Jsonb({"api": True})),
        ).fetchone()
        state = dict(session.get("state") or {})
        worldline = state.setdefault("worldline", {})
        variables = worldline.setdefault("user_variables", {})
        variables[key] = {"value": value, "source": source, "locked": True}
        db.execute(
            "update game_sessions set state = %s, worldline = %s, updated_at = now(), row_version = row_version + 1 where id = %s",
            (Jsonb(state), Jsonb(worldline), session["id"]),
        )
    return expose(row)  # type: ignore[return-value]


def remove_worldline_variable(user_id: int, save_id: int, key: str) -> dict[str, Any]:
    key = _clean_text(key)
    if not key:
        raise ValueError("变量名不能为空")
    session = ensure_game_session(user_id, save_id, _state_from_save(user_id, save_id))
    with connect() as db:
        db.execute("delete from worldline_variables where session_id = %s and key = %s", (session["id"], key))
        state = dict(session.get("state") or {})
        worldline = state.setdefault("worldline", {})
        variables = worldline.setdefault("user_variables", {})
        variables.pop(key, None)
        db.execute(
            "update game_sessions set state = %s, worldline = %s, updated_at = now(), row_version = row_version + 1 where id = %s",
            (Jsonb(state), Jsonb(worldline), session["id"]),
        )
    return {"removed": key}


def list_worldline_variables(user_id: int, save_id: int) -> dict[str, Any]:
    """前端面板用：列出某存档的所有 worldline 变量。"""
    init_db()
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        rows = _db_select_worldline_variables(db, save_id)
    return {"items": [expose(r) for r in rows], "total": len(rows)}
