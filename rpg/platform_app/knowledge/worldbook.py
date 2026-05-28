from __future__ import annotations

from typing import Any

from platform_app.db import connect, expose, init_db, limit_value, page_payload
from platform_app.knowledge._utils import _cursor_int, _require_script


def list_worldbook_entries(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = db.execute(
            """
            select * from worldbook_entries
            where script_id = %s and (%s::bigint is null or id < %s)
            order by priority desc, id desc
            limit %s
            """,
            (script_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)
