"""knowledge._worldline_repo — worldline 的 SQL 层 (private)."""
from __future__ import annotations


def _db_select_worldline_variables(db, save_id: int) -> list:
    """repository: 按 save_id 查所有 worldline_variables，返回 rows。"""
    return db.execute(
        """
        select wv.* from worldline_variables wv
        join game_sessions s on s.id = wv.session_id
        where s.save_id = %s
        order by wv.updated_at desc, wv.id desc
        """,
        (save_id,),
    ).fetchall()
