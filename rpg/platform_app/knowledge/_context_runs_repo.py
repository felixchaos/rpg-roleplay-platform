"""knowledge._context_runs_repo — context_runs 的 SQL 层 (private)."""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


def _db_update_context_run_status(db, run_id: int, status: str, error: str, duration_ms: int | None) -> None:
    """repository: 更新 context_run 的 status/error/duration_ms。"""
    if duration_ms is None:
        db.execute(
            "update context_runs set status = %s, error = %s where id = %s",
            (status, error, run_id),
        )
    else:
        db.execute(
            "update context_runs set status = %s, error = %s, duration_ms = %s where id = %s",
            (status, error, int(duration_ms), run_id),
        )


def _db_insert_turn_messages(db, session_id: int, save_id: int, turn: int, player_input: str, gm_output: str, metadata: dict[str, Any]) -> tuple:
    """repository: 插入一对 user/assistant 消息，返回 (user_row, gm_row)。

    opening 等无真实玩家输入的场景,player_input 为空 → 跳过 user 行,
    避免 materialize() 重建 history 时出现空的玩家气泡。
    """
    user_msg = None
    if player_input and player_input.strip():
        user_msg = db.execute(
            """
            insert into messages(session_id, save_id, turn, role, content, metadata)
            values (%s, %s, %s, 'user', %s, %s)
            returning *
            """,
            (session_id, save_id, turn, player_input, Jsonb(metadata)),
        ).fetchone()
    gm_msg = db.execute(
        """
        insert into messages(session_id, save_id, turn, role, content, metadata)
        values (%s, %s, %s, 'assistant', %s, %s)
        returning *
        """,
        (session_id, save_id, turn, gm_output, Jsonb(metadata)),
    ).fetchone()
    return user_msg, gm_msg


def _db_select_context_runs(db, save_id: int, before_id: int | None, page_limit: int) -> list:
    """repository: 按 save_id/cursor 分页查 context_runs，返回 rows。"""
    return db.execute(
        """
        select * from context_runs
        where save_id = %s and (%s::bigint is null or id < %s)
        order by id desc
        limit %s
        """,
        (save_id, before_id, before_id, page_limit + 1),
    ).fetchall()
