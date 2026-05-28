"""Seed the branch tree for a save, and migrate legacy nodes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from platform_app.branches._helpers import (
    BRANCH_STATE_DIR,
    MAIN_REF,
    load_state,
    rough_summary,
    round_preview,
    snapshot_for_history,
    write_snapshot,
)
from platform_app.branches.commits import _insert_commit
from platform_app.branches.refs import _ensure_active_ref, _set_save_active, _upsert_ref
from platform_app.db import connect, init_db


def seed_tree(save_id: int, state_path: str) -> None:
    """Seed or migrate the immutable branch graph for one save."""
    from platform_app.branches.maintenance import ensure_state_snapshots, ensure_summaries

    init_db()
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        if db.execute("select 1 from branch_commits where save_id = %s limit 1", (save_id,)).fetchone():
            ensure_state_snapshots(db, save_id)
            ensure_summaries(db, save_id)
            _ensure_active_ref(db, save_id)
            return
        if db.execute("select 1 from branch_nodes where save_id = %s limit 1", (save_id,)).fetchone():
            _migrate_legacy_nodes(db, save_id)
            ensure_state_snapshots(db, save_id)
            ensure_summaries(db, save_id)
            _ensure_active_ref(db, save_id)
            return

        save_row = db.execute("select state_snapshot from game_saves where id = %s", (save_id,)).fetchone()
        raw_snapshot = (save_row or {}).get("state_snapshot") if isinstance(save_row, dict) else None
        if isinstance(raw_snapshot, dict) and raw_snapshot:
            data = json.loads(json.dumps(raw_snapshot, ensure_ascii=False))
        else:
            data = load_state(Path(state_path))
        root_snapshot = snapshot_for_history(data, 0)
        root_state = write_snapshot(save_id, 0, root_snapshot)
        root = _insert_commit(
            db,
            save_id=save_id,
            parent_id=None,
            turn_index=0,
            kind="root",
            title="开始",
            message="存档起点",
            summary="存档起点",
            content_preview="存档起点",
            state_path=root_state,
            state_snapshot=root_snapshot,
            metadata={"source": "seed"},
        )
        parent_id = root["id"]
        history = list(data.get("history") or [])
        history_index = 0
        turn = 1
        while history_index < len(history):
            player_msg = history[history_index] if history[history_index].get("role") == "user" else None
            if player_msg:
                history_index += 1
            gm_msg = None
            if history_index < len(history) and history[history_index].get("role") != "user":
                gm_msg = history[history_index]
                history_index += 1
            elif not player_msg and history_index < len(history):
                gm_msg = history[history_index]
                history_index += 1
            player_text = (player_msg or {}).get("content", "")
            gm_text = (gm_msg or {}).get("content", "")
            snapshot_data = snapshot_for_history(data, history_index)
            snapshot = write_snapshot(save_id, turn, snapshot_data)
            row = _insert_commit(
                db,
                save_id=save_id,
                parent_id=parent_id,
                turn_index=turn,
                kind="round",
                title=f"第 {turn} 回合",
                message=rough_summary(player_text, gm_text),
                summary=rough_summary(player_text, gm_text),
                content_preview=round_preview(player_text, gm_text),
                state_path=snapshot,
                state_snapshot=snapshot_data,
                player_input=player_text,
                gm_output=gm_text,
                metadata={"source": "seed", "history_index": history_index},
            )
            parent_id = row["id"]
            turn += 1
        ref = _upsert_ref(db, save_id, MAIN_REF, parent_id, active=True)
        _set_save_active(db, save_id, parent_id, ref["id"])


def _seed_and_bootstrap(owner_id: int, save_id: int, state_path: str, user_id: int | None) -> dict[str, Any]:
    seed_tree(save_id, state_path)
    from platform_app.branches.runtime import bootstrap_runtime_binding
    return bootstrap_runtime_binding(user_id=user_id or owner_id)


def _migrate_legacy_nodes(db, save_id: int) -> None:
    rows = db.execute("select * from branch_nodes where save_id = %s order by id", (save_id,)).fetchall()
    id_map: dict[int, int] = {}
    for row in rows:
        parent_id = id_map.get(row.get("parent_id"))
        state_snapshot = load_state(Path(str(row.get("state_path") or "")))
        commit = _insert_commit(
            db,
            save_id=save_id,
            parent_id=parent_id,
            turn_index=int(row.get("turn_index") or 0),
            kind=str(row.get("role") or "round"),
            title=str(row.get("title") or ""),
            message=str(row.get("summary") or row.get("title") or ""),
            summary=str(row.get("summary") or ""),
            content_preview=str(row.get("content_preview") or ""),
            state_path=str(row.get("state_path") or ""),
            state_snapshot=state_snapshot,
            metadata={"source": "legacy_branch_nodes", "legacy_node_id": row["id"]},
        )
        id_map[row["id"]] = commit["id"]
        if row.get("role") == "branch":
            _upsert_ref(db, save_id, f"refs/heads/legacy-{row['id']}", commit["id"], active=False)
    save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
    active_old = save.get("active_branch_node_id") if save else None
    active_commit_id = id_map.get(active_old) if active_old else None
    if not active_commit_id and rows:
        active_commit_id = id_map.get(rows[-1]["id"])
    if active_commit_id:
        ref = _upsert_ref(db, save_id, MAIN_REF, active_commit_id, active=True)
        _set_save_active(db, save_id, active_commit_id, ref["id"])
