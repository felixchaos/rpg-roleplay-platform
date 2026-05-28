"""branches._runtime_repo — runtime 的 SQL 层 (private)."""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


def _db_mark_checkout_dirty(db, save_id: int, runtime_state: dict[str, Any], snap_hash: str, turn: int) -> None:
    """repository: 将 runtime_checkouts.dirty 置为 true 并更新 snapshot。"""
    db.execute(
        """
        update runtime_checkouts
           set state_snapshot = %s,
               snapshot_hash = %s,
               turn_runtime = %s,
               dirty = (snapshot_hash <> %s OR turn_runtime <> %s),
               row_version = row_version + 1,
               updated_at = now()
         where save_id = %s
        """,
        (Jsonb(runtime_state), snap_hash, turn, snap_hash, turn, save_id),
    )
