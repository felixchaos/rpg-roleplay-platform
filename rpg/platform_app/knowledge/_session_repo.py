"""knowledge._session_repo — session 的 SQL 层 (private)."""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


def _db_upsert_game_session(db, save_id: int, book_id: int, script_id: int, user_id: int, title: str, payload: dict[str, Any]):
    """repository: upsert game_sessions 并返回 row。"""
    return db.execute(
        """
        insert into game_sessions(
          save_id, book_id, script_id, user_id, title, state,
          memory_mode, permission_mode, worldline, turn
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict(save_id) do update set
          book_id = excluded.book_id,
          script_id = excluded.script_id,
          title = excluded.title,
          state = excluded.state,
          memory_mode = excluded.memory_mode,
          permission_mode = excluded.permission_mode,
          worldline = excluded.worldline,
          turn = excluded.turn,
          row_version = game_sessions.row_version + 1,
          updated_at = now()
        returning *
        """,
        (
            save_id,
            book_id,
            script_id,
            user_id,
            title,
            Jsonb(payload),
            (payload.get("memory") or {}).get("mode", "normal"),
            (payload.get("permissions") or {}).get("mode", "full_access"),
            Jsonb(payload.get("worldline") or {}),
            int(payload.get("turn") or 0),
        ),
    ).fetchone()
