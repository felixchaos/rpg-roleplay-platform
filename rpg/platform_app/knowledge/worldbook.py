from __future__ import annotations

from typing import Any

from platform_app.db import connect, init_db, limit_value, page_payload
from platform_app.knowledge._utils import _cursor_int, _require_script
from platform_app.knowledge._worldbook_repo import _db_select_worldbook_entries


def list_worldbook_entries(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = _db_select_worldbook_entries(db, script_id, before_id, page_limit)
    return page_payload(rows, page_limit)
