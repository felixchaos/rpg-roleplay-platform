"""knowledge._memory_repo — memory 的 SQL 层 (private)."""
from __future__ import annotations

from typing import Any


def _db_select_memories(db, save_id: int, bucket: str | None, page_limit: int, before_id: int | None) -> list:
    """repository: 按 save_id/bucket/cursor 查 memories，返回 rows。"""
    params: list[Any] = [save_id]
    where_clause = "s.save_id = %s"
    if bucket:
        where_clause += " and m.bucket = %s"
        params.append(bucket)
    where_clause += " and (%s::bigint is null or m.id < %s)"
    params.extend([before_id, before_id])
    params.append(page_limit + 1)
    return db.execute(
        f"""
        select m.* from memories m
        join game_sessions s on s.id = m.session_id
        where {where_clause}
        order by m.importance desc, m.id desc
        limit %s
        """,
        tuple(params),
    ).fetchall()
