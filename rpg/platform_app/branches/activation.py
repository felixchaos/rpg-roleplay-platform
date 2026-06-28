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
        # 截断超出目标 commit turn 的 messages:新建分支后旧分支残留的消息
        # 会被 materialize() 重建 history 时拉出来,导致 Game Console 显示旧分支对话。
        target_turn = int(node.get("turn_index") or 0)
        db.execute(
            "delete from messages where save_id = %s and turn > %s",
            (save_id, target_turn),
        )
    # [hotfix] runtime 写必须在 with 块外(advisory 锁已释放 + 本 conn 已归还池)。
    # 原来锁内调 activate_state_snapshot → _db_write_runtime 会【另开一条 conn】去
    # `update game_saves set last_played_at where id=save_id`,而本事务未提交的 _set_save_active
    # 已持有该 game_saves 行锁 → 新 conn 等行锁、本 conn 在 Python 里等新 conn 返回 → 互等死锁。
    # 因本 conn 是 idle-in-transaction(非 DB 阻塞),Postgres 检测不到、又无 lock_timeout → 永久挂,
    # 开档/切档每次必卡 "signal timed out"。锁外写不影响指针正确性(指针在锁内已落定)。
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
        # 截断超出目标 commit turn 的 messages(与 continue_from 同理)。
        target_turn = int(node.get("turn_index") or 0)
        db.execute(
            "delete from messages where save_id = %s and turn > %s",
            (save_id, target_turn),
        )
    # [hotfix] runtime 写移出 with 块,避免锁内嵌套连接 update 同一 game_saves 行的自死锁(见 continue_from)。
    runtime_info = _runtime_module.activate_state_snapshot(user_id, save_id, node_id, state_snapshot, state_path, ref_id=active_ref_id)
    result = tree(user_id, save_id)
    result["ok"] = True
    result["runtime"] = runtime_info
    result["game_url"] = runtime_info["game_url"]
    result["runtime_url"] = runtime_info["game_url"]
    result["active_branch_node_id"] = node_id
    result["active_commit_id"] = node_id
    return result


def _refresh_tavern_cards_from_library(db, user_id: int, save: dict, snap: dict) -> None:
    """酒馆激活时:用统一卡库(character_cards)最新值覆盖快照里陈旧的 character/persona。
    合并(update)而非整体替换,保留快照里卡字段以外的运行时键。修复:编辑设定后侧栏不更新、
    换人设图后头像不更新(avatar_path 随之刷新)。"""
    tav = snap.get("tavern")
    if not isinstance(tav, dict):
        return
    try:
        from platform_app.api._card_dto import card_to_dto
    except Exception:
        return
    for fk_col, key in (("tavern_character_card_id", "character"), ("tavern_persona_card_id", "persona")):
        cid = save.get(fk_col)
        if not cid:
            continue
        row = db.execute(
            "select * from character_cards where id = %s and user_id = %s",
            (int(cid), user_id),
        ).fetchone()
        if not row:
            continue
        try:
            fresh = card_to_dto(row)
        except Exception:
            continue
        cur = tav.get(key)
        if isinstance(cur, dict):
            cur.update(fresh)
        else:
            tav[key] = fresh


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
        # tavern:快照里的 character/persona 是建档时的【denormalized 副本】,用户之后在统一
        # 角色卡库(character_cards)里编辑/换人设图后,快照会陈旧 → 侧栏「编辑后不显示 / 人设图不显示」。
        # 激活时按 FK 从 character_cards 重读最新值覆盖,使快照=统一卡库的镜像(web + iOS 同源 /api/state,一并修复)。
        if save.get("save_kind") == "tavern" and isinstance(state_snapshot, dict):
            _refresh_tavern_cards_from_library(db, user_id, save, state_snapshot)
        active_ref_id = ref["id"]
        active_commit_id = commit_row["id"]
    # [hotfix] runtime 写移出 with 块,避免锁内嵌套连接 update 同一 game_saves 行的自死锁(见 continue_from)。
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
