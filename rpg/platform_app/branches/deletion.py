"""Subtree deletion and rollback operations."""
from __future__ import annotations

import time
from typing import Any

from platform_app.branches._helpers import MAIN_REF, _unlink_branch_state, commit_state
from platform_app.branches.commits import _commit_for_user
from platform_app.branches.refs import (
    _find_or_create_ref_for_commit,
    _set_save_active,
    _upsert_ref,
    _write_checkout,
)
from platform_app.branches.tree_ops import collect_ids, round_start_node, tree
from platform_app.db import connect, expose, init_db
from platform_app import runtime as _runtime_module


def delete_subtree(user_id: int, node_id: int) -> dict[str, Any]:
    init_db()
    runtime_payload: dict[str, Any] | None = None
    with connect() as db:
        node = _commit_for_user(db, user_id, node_id)
        if not node:
            raise ValueError("无权访问该分支节点")
        node = round_start_node(db, node)
        if node["parent_id"] is None:
            raise ValueError("不能删除根节点")
        ids = collect_ids(db, node["id"])
        paths = [
            row["state_path"]
            for row in db.execute("select state_path from branch_commits where id = any(%s)", (ids,)).fetchall()
        ]
        save = db.execute("select * from game_saves where id = %s", (node["save_id"],)).fetchone()
        fallback = db.execute(
            "select * from branch_commits where id = %s and save_id = %s",
            (node["parent_id"], node["save_id"]),
        ).fetchone()
        active_commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
        active_deleted = active_commit_id in ids
        db.execute("delete from branch_refs where save_id = %s and target_commit_id = any(%s)", (node["save_id"], ids))
        db.execute("delete from branch_commits where id = any(%s)", (ids,))
        if active_deleted and fallback:
            ref = _upsert_ref(db, node["save_id"], MAIN_REF, fallback["id"], active=True)
            _set_save_active(db, node["save_id"], fallback["id"], ref["id"])
            _write_checkout(db, user_id, node["save_id"], ref["id"], fallback["id"])
            runtime_payload = _runtime_module.activate_state_snapshot(
                user_id,
                node["save_id"],
                fallback["id"],
                commit_state(fallback),
                fallback["state_path"],
                ref_id=ref["id"],
            )
        save_id = node["save_id"]
    for path in paths:
        _unlink_branch_state(path)
    result = tree(user_id, save_id)
    if runtime_payload:
        result["runtime"] = runtime_payload
    return result


def rollback_to_message(
    user_id: int,
    save_id: int,
    message_index: int,
) -> dict[str, Any]:
    """task 116c — 删除消息 N 及之后所有 → 软回滚到 turn (N//2 - 1) 的 round commit。"""
    init_db()
    msg_index = int(message_index)
    if msg_index < 0:
        raise ValueError("message_index 不能小于 0")
    deleted_turn = msg_index // 2
    target_turn = deleted_turn - 1
    runtime_payload: dict[str, Any] | None = None

    with connect() as db:
        save = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档,或存档不存在")

        if target_turn < 0:
            target_commit = db.execute(
                """
                select * from branch_commits
                where save_id = %s and kind = 'root'
                order by id asc limit 1
                """,
                (save_id,),
            ).fetchone()
            if not target_commit:
                raise ValueError("找不到 root commit,无法回到开局之前")
        else:
            target_commit = db.execute(
                """
                select * from branch_commits
                where save_id = %s and turn_index = %s and kind in ('round', 'gm')
                order by id desc limit 1
                """,
                (save_id, target_turn),
            ).fetchone()
            if not target_commit:
                raise ValueError(f"找不到 turn {target_turn} 的 commit,无法回滚")

        current_commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
        trash_ref = None
        if current_commit_id and current_commit_id != target_commit["id"]:
            ts = time.strftime("%Y%m%d-%H%M%S")
            trash_name = f"refs/trash/{ts}-msg{msg_index}"
            trash_ref = _upsert_ref(
                db, save_id, trash_name, current_commit_id,
                active=False, kind="trash",
            )

        new_ref = _find_or_create_ref_for_commit(db, user_id, target_commit)
        _set_save_active(db, save_id, target_commit["id"], new_ref["id"])
        _write_checkout(db, user_id, save_id, new_ref["id"], target_commit["id"])

        deleted_messages = db.execute(
            "delete from messages where save_id = %s and turn >= %s returning id",
            (save_id, deleted_turn),
        ).fetchall()
        n_msgs = len(deleted_messages or [])

        deleted_anchors = db.execute(
            "delete from save_timeline_anchors where save_id = %s and turn_index >= %s returning id",
            (save_id, deleted_turn),
        ).fetchall()
        n_anchors = len(deleted_anchors or [])

        deleted_runs = db.execute(
            """
            delete from context_runs
            where session_id in (select id from game_sessions where save_id = %s)
              and turn >= %s
            returning id
            """,
            (save_id, deleted_turn),
        ).fetchall()
        n_runs = len(deleted_runs or [])

        phase_fixed = 0
        phase_dropped = 0
        affected_phases = db.execute(
            """
            select id, phase_index, turn_start, turn_end from save_phase_digests
            where save_id = %s and turn_end >= %s
            order by phase_index
            """,
            (save_id, deleted_turn),
        ).fetchall()
        for ph in affected_phases:
            if ph["turn_start"] >= deleted_turn:
                db.execute("delete from save_phase_digests where id = %s", (ph["id"],))
                phase_dropped += 1
            else:
                db.execute(
                    "update save_phase_digests set turn_end = %s, updated_at = now() where id = %s",
                    (deleted_turn - 1, ph["id"]),
                )
                phase_fixed += 1

        target_state = commit_state(target_commit)
        state_path = target_commit.get("state_path") or ""
        ref_id_for_runtime = new_ref["id"]

    runtime_payload = _runtime_module.activate_state_snapshot(
        user_id, save_id, target_commit["id"], target_state, state_path, ref_id=ref_id_for_runtime,
    )

    result = tree(user_id, save_id)
    result["ok"] = True
    result["runtime"] = runtime_payload
    result["game_url"] = runtime_payload.get("game_url")
    result["active_commit_id"] = target_commit["id"]
    result["active_branch_node_id"] = target_commit["id"]
    result["restored_turn"] = target_turn if target_turn >= 0 else -1
    result["deleted"] = {
        "messages": n_msgs,
        "timeline_anchors": n_anchors,
        "context_runs": n_runs,
        "phase_digests_truncated": phase_fixed,
        "phase_digests_dropped": phase_dropped,
    }
    result["trash_ref"] = (expose(trash_ref) if trash_ref else None)
    return result
