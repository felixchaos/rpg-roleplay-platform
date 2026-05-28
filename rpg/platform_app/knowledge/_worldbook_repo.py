"""knowledge._worldbook_repo — worldbook 的 SQL 层 (private)."""
from __future__ import annotations


def _db_select_worldbook_entries(db, script_id: int, before_id: int | None, page_limit: int) -> list:
    """repository: 按 script_id/cursor 分页查 worldbook_entries，返回 rows。"""
    return db.execute(
        """
        select * from worldbook_entries
        where script_id = %s and (%s::bigint is null or id < %s)
        order by priority desc, id desc
        limit %s
        """,
        (script_id, before_id, before_id, page_limit + 1),
    ).fetchall()
