"""Node activation: continue_from, activate_node, activate_save."""
from __future__ import annotations

import secrets
from typing import Any

from platform_app import runtime as _runtime_module
from platform_app.branches._helpers import acquire_save_advisory_lock, commit_state
from platform_app.branches.commits import _commit_for_user
from platform_app.branches.refs import (
    _find_or_create_ref_for_commit,
    _set_save_active,
    _upsert_ref,
    _write_checkout,
)
from platform_app.branches.seed import seed_tree
from platform_app.branches.tree_ops import tree
from platform_app.db import connect, expose, init_db


def continue_from(user_id: int, node_id: int) -> dict[str, Any]:
    init_db()
    active_commit_id = 0
    active_ref_id: int | None = None
    save_id = 0
    state_path = ""
    ref_row: dict[str, Any] | None = None
    with connect() as db:
        node = _commit_for_user(db, user_id, node_id)
        if not node:
            raise ValueError("无权访问该分支节点")

        save_id = node["save_id"]
        # 与回合提交 / autosave 同 key 的锁:防并发覆盖 game_saves 活跃指针(读指针前取)。
        acquire_save_advisory_lock(db, save_id, user_id)
        state_snapshot = commit_state(node)
        state_path = node["state_path"]
        ref = _upsert_ref(
            db,
            node["save_id"],
            f"refs/heads/from-{node['id']}-{secrets.token_hex(4)}",
            node["id"],
            active=True,
        )
        active_commit_id = node["id"]
        active_ref_id = ref["id"]
        ref_row = ref
        _set_save_active(db, save_id, active_commit_id, active_ref_id)
        _write_checkout(db, user_id, save_id, active_ref_id, active_commit_id)
        # 在 advisory 锁仍持有(with 未退出)时写 runtime,串行化 user_runtime 与 game_saves 指针;
        # 否则锁释放后再写,两 tab 并发可让 user_runtime 指向旧节点、与 game_saves 不一致(下回合从错分支起)。
        runtime_info = _runtime_module.activate_state_snapshot(user_id, save_id, active_commit_id, state_snapshot, state_path, ref_id=active_ref_id)
    result = tree(user_id, save_id)
    result["ok"] = True
    result["runtime"] = runtime_info
    result["game_url"] = runtime_info["game_url"]
    result["runtime_url"] = runtime_info["game_url"]
    result["active_ref"] = expose(ref_row) if ref_row else None
    result["active_branch_node_id"] = active_commit_id
    result["active_commit_id"] = active_commit_id
    return result


def activate_node(user_id: int, node_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        node = _commit_for_user(db, user_id, node_id)
        if not node:
            raise ValueError("无权访问该分支节点")
        # 与回合提交 / autosave 同 key 的锁:防并发覆盖 game_saves 活跃指针(写指针前取)。
        acquire_save_advisory_lock(db, node["save_id"], user_id)
        ref = _find_or_create_ref_for_commit(db, user_id, node)
        _set_save_active(db, node["save_id"], node["id"], ref["id"])
        _write_checkout(db, user_id, node["save_id"], ref["id"], node["id"])
        save_id = node["save_id"]
        state_path = node["state_path"]
        state_snapshot = commit_state(node)
        active_ref_id = ref["id"]
        # 锁内写 runtime(串行化 user_runtime 与 game_saves 指针,见 continue_from 注释)
        runtime_info = _runtime_module.activate_state_snapshot(user_id, save_id, node_id, state_snapshot, state_path, ref_id=active_ref_id)
    result = tree(user_id, save_id)
    result["ok"] = True
    result["runtime"] = runtime_info
    result["game_url"] = runtime_info["game_url"]
    result["runtime_url"] = runtime_info["game_url"]
    result["active_branch_node_id"] = node_id
    result["active_commit_id"] = node_id
    return result


def activate_save(user_id: int, save_id: int) -> dict[str, Any]:
    """task 30：切到目标 save 的当前激活分支（或没有就 root），并真的切换 user_runtime。"""
    init_db()
    with connect() as db:
        # 与回合提交 / autosave 同 key 的锁:在读 game_saves 活跃指针之前取,防并发覆盖。
        acquire_save_advisory_lock(db, save_id, user_id)
        save = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        node_id = save.get("active_branch_node_id")
        commit_row = None
        if node_id:
            commit_row = db.execute(
                "select * from branch_commits where id = %s and save_id = %s",
                (int(node_id), save_id),
            ).fetchone()
        if not commit_row:
            commit_row = db.execute(
                "select * from branch_commits where save_id = %s order by turn_index asc, id asc limit 1",
                (save_id,),
            ).fetchone()
        if not commit_row:
            seed_tree(save_id, save.get("state_path") or "")
            commit_row = db.execute(
                "select * from branch_commits where save_id = %s order by turn_index asc, id asc limit 1",
                (save_id,),
            ).fetchone()
        if not commit_row:
            raise ValueError("save 没有任何 commit，无法激活")
        ref = _find_or_create_ref_for_commit(db, user_id, commit_row)
        _set_save_active(db, save_id, commit_row["id"], ref["id"])
        _write_checkout(db, user_id, save_id, ref["id"], commit_row["id"])
        state_snapshot = commit_state(commit_row)
        state_path = commit_row.get("state_path") or save.get("state_path") or ""
        active_ref_id = ref["id"]
        active_commit_id = commit_row["id"]
        # 锁内写 runtime(串行化 user_runtime 与 game_saves 指针,见 continue_from 注释)
        runtime_info = _runtime_module.activate_state_snapshot(
            user_id, save_id, active_commit_id, state_snapshot, state_path, ref_id=active_ref_id,
        )
    return {
        "ok": True,
        "active_save_id": save_id,
        "active_commit_id": active_commit_id,
        "active_branch_node_id": active_commit_id,
        "runtime": runtime_info,
    }
