"""Subtree deletion and rollback operations."""
from __future__ import annotations

import time
from typing import Any

from platform_app import runtime as _runtime_module
from platform_app.branches._helpers import (
    MAIN_REF,
    _unlink_branch_state,
    acquire_save_advisory_lock,
    commit_state,
)
from platform_app.branches.commits import _commit_for_user
from platform_app.branches.refs import (
    _find_or_create_ref_for_commit,
    _set_save_active,
    _upsert_ref,
    _write_checkout,
)
from platform_app.branches.tree_ops import collect_ids, message_row_by_index, round_start_node, tree
from platform_app.db import connect, expose, init_db


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
        # 与回合提交 / autosave 同 key 的锁:删子树可能改 game_saves 活跃指针(active 被删时回退到
        # fallback),读 game_saves 之前取,防并发回合在我们读指针与写 fallback 之间提交被覆盖。
        acquire_save_advisory_lock(db, node["save_id"], user_id)
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
    runtime_payload: dict[str, Any] | None = None

    with connect() as db:
        # 与回合提交 / autosave 同 key 的锁:回滚要把 game_saves 活跃指针软回退到历史 commit,
        # 读 game_saves 之前取,防并发回合在我们读指针与写回退之间提交把回滚冲掉。
        acquire_save_advisory_lock(db, save_id, user_id)
        save = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档,或存档不存在")

        target_msg = message_row_by_index(db, save_id, msg_index)
        if target_msg:
            deleted_turn = int(target_msg["turn"])
            target_message_id = int(target_msg["id"])
            target_message_role = str(target_msg["role"] or "")
        else:
            deleted_turn = msg_index // 2
            target_message_id = None
            target_message_role = "user" if msg_index % 2 == 0 else "assistant"
        target_turn = deleted_turn - 1

        target_commit = None
        if target_turn >= 0:
            target_commit = db.execute(
                """
                select * from branch_commits
                where save_id = %s and turn_index = %s and kind in ('round', 'gm', 'player')
                order by id desc limit 1
                """,
                (save_id, target_turn),
            ).fetchone()
        if not target_commit and target_turn <= 0:
            target_commit = db.execute(
                """
                select * from branch_commits
                where save_id = %s and kind = 'root'
                order by id asc limit 1
                """,
                (save_id,),
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

        if target_message_id is not None:
            deleted_messages = db.execute(
                """
                delete from messages
                where save_id = %s
                  and (turn > %s or (turn = %s and id >= %s))
                returning id
                """,
                (save_id, deleted_turn, deleted_turn, target_message_id),
            ).fetchall()
        else:
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
        "from_role": target_message_role,
        "timeline_anchors": n_anchors,
        "context_runs": n_runs,
        "phase_digests_truncated": phase_fixed,
        "phase_digests_dropped": phase_dropped,
    }
    result["trash_ref"] = (expose(trash_ref) if trash_ref else None)
    return result


def rewind_last_round(user_id: int, save_id: int) -> dict[str, Any] | None:
    """反馈#42 — 重写型 /set 专用:把最近一个回合(round)整体软回滚。

    与 rollback_to_message 同策略(移动活跃指针 + trash ref 保活旧回合 + 清理本回合
    messages/anchors/context_runs/phase_digests),但**不需要 message_index**,固定回滚
    "当前活跃回合",并额外**返回回退后的状态快照 + 被回滚回合的原始玩家输入**,供 chat
    pipeline 在纠正后的状态下用原输入重演本轮(避免被纠正的旧叙事留在上下文里让 GM 圆场)。

    无可回滚回合(活跃指针指向根节点 / 缺失)时返回 None,调用方应退化为普通 /set。
    """
    init_db()
    with connect() as db:
        acquire_save_advisory_lock(db, save_id, user_id)
        save = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档,或存档不存在")
        active_id = save.get("active_commit_id") or save.get("active_branch_node_id")
        if not active_id:
            return None
        cur = db.execute(
            "select * from branch_commits where id = %s and save_id = %s",
            (active_id, save_id),
        ).fetchone()
        if not cur:
            return None
        cur = round_start_node(db, cur)
        if cur.get("parent_id") is None or str(cur.get("kind") or "") == "root":
            return None  # 根节点,没有上一轮可回滚
        parent = db.execute(
            "select * from branch_commits where id = %s and save_id = %s",
            (cur["parent_id"], save_id),
        ).fetchone()
        if not parent:
            return None

        deleted_turn = int(cur.get("turn_index") or 0)
        redo_input = str(cur.get("player_input") or "")

        # 旧回合进 trash ref(可恢复,不硬删 commit)
        ts = time.strftime("%Y%m%d-%H%M%S")
        trash_ref = _upsert_ref(
            db, save_id, f"refs/trash/{ts}-rewrite", cur["id"],
            active=False, kind="trash",
        )
        # 活跃指针软回退到 parent
        new_ref = _find_or_create_ref_for_commit(db, user_id, parent)
        _set_save_active(db, save_id, parent["id"], new_ref["id"])
        _write_checkout(db, user_id, save_id, new_ref["id"], parent["id"])

        # 清理本回合的派生数据(让前端 reload / 历史段重建都看不到被回滚的旧叙事)
        deleted_messages = db.execute(
            "delete from messages where save_id = %s and turn >= %s returning id",
            (save_id, deleted_turn),
        ).fetchall()
        deleted_anchors = db.execute(
            "delete from save_timeline_anchors where save_id = %s and turn_index >= %s returning id",
            (save_id, deleted_turn),
        ).fetchall()
        db.execute(
            """
            delete from context_runs
            where session_id in (select id from game_sessions where save_id = %s)
              and turn >= %s
            """,
            (save_id, deleted_turn),
        )
        for ph in db.execute(
            "select id, turn_start, turn_end from save_phase_digests "
            "where save_id = %s and turn_end >= %s",
            (save_id, deleted_turn),
        ).fetchall():
            if int(ph["turn_start"]) >= deleted_turn:
                db.execute("delete from save_phase_digests where id = %s", (ph["id"],))
            else:
                db.execute(
                    "update save_phase_digests set turn_end = %s, updated_at = now() where id = %s",
                    (deleted_turn - 1, ph["id"]),
                )

        reverted_state = commit_state(parent)

    return {
        "ok": True,
        "reverted_state": reverted_state,
        "redo_player_input": redo_input,
        "restored_turn": int(parent.get("turn_index") or 0),
        "deleted_turn": deleted_turn,
        "deleted_messages": len(deleted_messages or []),
        "deleted_anchors": len(deleted_anchors or []),
        "trash_ref": (expose(trash_ref) if trash_ref else None),
    }
