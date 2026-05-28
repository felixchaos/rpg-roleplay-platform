"""Maintenance helpers: ensure_summaries, ensure_state_snapshots."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from platform_app.branches._helpers import load_state, rough_summary
from platform_app.branches.commits import _state_snapshot_hash


def ensure_summaries(db, save_id: int) -> None:
    rows = db.execute("select * from branch_commits where save_id = %s order by id", (save_id,)).fetchall()
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        current = row.get("summary") or ""
        if current and current != "空回合" and not current.startswith("我好像"):
            continue
        player_text = row.get("player_input") or ""
        gm_text = row.get("gm_output") or ""
        if not player_text and not gm_text:
            if row["kind"] == "gm":
                parent = by_id.get(row.get("parent_id"))
                if parent and parent["kind"] == "player" and parent["turn_index"] == row["turn_index"]:
                    player_text = parent.get("content_preview", "")
                gm_text = row.get("content_preview", "")
            elif row["kind"] == "player":
                player_text = row.get("content_preview", "")
            elif row["kind"] == "round":
                gm_text = row.get("content_preview", "")
            else:
                gm_text = row.get("content_preview", "") or row.get("title", "")
        db.execute("update branch_commits set summary = %s where id = %s", (rough_summary(player_text, gm_text), row["id"]))


def ensure_state_snapshots(db, save_id: int) -> None:
    rows = db.execute(
        """
        select id, state_path, state_snapshot
        from branch_commits
        where save_id = %s
          and (state_snapshot = '{}'::jsonb or state_snapshot is null)
        order by id
        """,
        (save_id,),
    ).fetchall()
    for row in rows:
        snapshot = load_state(Path(row.get("state_path") or ""))
        db.execute(
            """
            update branch_commits
            set state_snapshot = %s,
                tree_hash = %s,
                row_version = row_version + 1
            where id = %s
            """,
            (Jsonb(snapshot), _state_snapshot_hash(snapshot), row["id"]),
        )
