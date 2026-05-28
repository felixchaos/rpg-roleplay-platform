"""Commit insertion and hashing utilities."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from platform_app.branches._helpers import _snapshot_quality, load_state  # noqa: F401


def _object_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_file_hash(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return ""


def _state_snapshot_hash(state: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(state or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except Exception:
        return ""


def _insert_commit(
    db,
    *,
    save_id: int,
    parent_id: int | None,
    turn_index: int,
    kind: str,
    title: str,
    message: str,
    summary: str,
    content_preview: str,
    state_path: str,
    state_snapshot: dict[str, Any] | None = None,
    player_input: str = "",
    gm_output: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata or {}
    snapshot = state_snapshot if isinstance(state_snapshot, dict) else load_state(Path(state_path))
    tree_hash = _state_snapshot_hash(snapshot) or _state_file_hash(state_path)
    object_hash = _object_hash(
        {
            "save_id": save_id,
            "parent_id": parent_id,
            "turn_index": turn_index,
            "kind": kind,
            "title": title,
            "message": message,
            "summary": summary,
            "content_preview": content_preview,
            "state_path": state_path,
            "tree_hash": tree_hash,
            "state_snapshot": snapshot,
            "player_input": player_input,
            "gm_output": gm_output,
            "metadata": metadata,
        }
    )
    return db.execute(
        """
        insert into branch_commits(
          save_id, parent_id, object_hash, tree_hash, turn_index, kind, title,
          message, summary, content_preview, state_path, state_snapshot, player_input, gm_output, metadata
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict(save_id, object_hash) do update
          set state_snapshot = branch_commits.state_snapshot,
              row_version = branch_commits.row_version
        returning *
        """,
        (
            save_id,
            parent_id,
            object_hash,
            tree_hash,
            int(turn_index or 0),
            kind,
            title,
            message,
            summary,
            content_preview,
            state_path,
            Jsonb(snapshot),
            player_input,
            gm_output,
            Jsonb(metadata),
        ),
    ).fetchone()


def _commit_for_user(db, user_id: int, commit_id: int) -> dict[str, Any] | None:
    row = db.execute(
        """
        select branch_commits.*, game_saves.user_id
        from branch_commits join game_saves on game_saves.id = branch_commits.save_id
        where branch_commits.id = %s
        """,
        (commit_id,),
    ).fetchone()
    if not row or int(row["user_id"]) != int(user_id):
        return None
    return row
