from __future__ import annotations

from typing import Any

from platform_app.db import connect, expose, init_db, limit_value, page_payload
from platform_app.knowledge._utils import _cursor_int


def list_memories(user_id: int, save_id: int, bucket: str | None = None, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    """前端面板用：列出某存档的记忆，可按 bucket 过滤。"""
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        params: list[Any] = [save_id]
        where_clause = "s.save_id = %s"
        if bucket:
            where_clause += " and m.bucket = %s"
            params.append(bucket)
        where_clause += " and (%s::bigint is null or m.id < %s)"
        params.extend([before_id, before_id])
        params.append(page_limit + 1)
        rows = db.execute(
            f"""
            select m.* from memories m
            join game_sessions s on s.id = m.session_id
            where {where_clause}
            order by m.importance desc, m.id desc
            limit %s
            """,
            tuple(params),
        ).fetchall()
    return page_payload(rows, page_limit)
