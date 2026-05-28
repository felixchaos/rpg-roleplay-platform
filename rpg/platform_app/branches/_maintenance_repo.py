"""branches._maintenance_repo — maintenance 的 SQL 层 (private)."""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


def _db_update_commit_snapshot(db, commit_id: int, snapshot: dict[str, Any], tree_hash: str) -> None:
    """repository: 回填 branch_commits 的 state_snapshot 和 tree_hash。"""
    db.execute(
        """
        update branch_commits
        set state_snapshot = %s,
            tree_hash = %s,
            row_version = row_version + 1
        where id = %s
        """,
        (Jsonb(snapshot), tree_hash, commit_id),
    )
