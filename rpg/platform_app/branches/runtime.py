"""Runtime turn recording, persistence, bootstrap, and dirty-marking."""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from platform_app import runtime as _runtime_module
from platform_app.branches._helpers import (
    _snapshot_quality,
    acquire_save_advisory_lock,
    commit_state,
    load_state,
    rough_summary,
    round_preview,
    tavern_card_cols,
    write_runtime_snapshot,
)
from platform_app.branches._runtime_repo import _db_mark_checkout_dirty
from platform_app.branches.commits import _insert_commit, _state_snapshot_hash
from platform_app.branches.refs import (
    _find_or_create_ref_for_commit,
    _set_save_active,
    _upsert_ref_by_id,
    _write_checkout,
)
from platform_app.branches.summary import schedule_llm_summary
from platform_app.db import connect, expose, init_db


def record_runtime_turn(
    player_input: str,
    gm_response: str,
    runtime_state_path: str | None = None,
    user_id: int | None = None,
    state_data: dict[str, Any] | None = None,
    _depth: int = 0,
) -> dict[str, Any]:
    """多用户安全：调用方应传 state_data=state.data 而不是依赖 runtime_state_path 读文件。"""
    meta = _runtime_module.read_runtime(user_id=user_id) or bootstrap_runtime_binding(user_id=user_id)
    if user_id and int(meta.get("user_id") or 0) not in {0, int(user_id)}:
        meta = bootstrap_runtime_binding(user_id=user_id)
    if not meta:
        return {"ok": False, "reason": "未激活存档分支 runtime"}
    save_id = int(meta.get("save_id") or 0)
    parent_id = int(meta.get("active_commit_id") or meta.get("active_branch_node_id") or 0)
    ref_id = int(meta.get("active_ref_id") or 0) or None
    if not save_id or not parent_id:
        return {"ok": False, "reason": "runtime 缺少存档或节点"}

    from state import SAVE_FILE
    state_path = Path(runtime_state_path or SAVE_FILE)
    if isinstance(state_data, dict):
        data = json.loads(json.dumps(state_data, ensure_ascii=False))
    else:
        data = load_state(state_path)
    turn = int(data.get("turn") or 0)
    summary = rough_summary(player_input, gm_response)
    preview = round_preview(player_input, gm_response)
    snapshot_path = write_runtime_snapshot(save_id, data)

    init_db()
    missing_parent = False
    with connect() as db:
        # 复用统一锁助手(失败上抛,不再静默吞掉致并发指针错乱)
        acquire_save_advisory_lock(db, save_id, user_id)
        parent = db.execute("select * from branch_commits where id = %s and save_id = %s", (parent_id, save_id)).fetchone()
        if not parent:
            missing_parent = True
        else:
            save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
            if user_id and (not save or int(save["user_id"]) != int(user_id)):
                return {"ok": False, "reason": "runtime 不属于当前用户"}
            if not ref_id:
                ref = _find_or_create_ref_for_commit(db, int(save["user_id"]), parent)
                ref_id = ref["id"]
            fresh_save = db.execute("select active_commit_id, active_branch_node_id from game_saves where id = %s", (save_id,)).fetchone()
            fresh_active = int(fresh_save.get("active_commit_id") or fresh_save.get("active_branch_node_id") or 0)
            if fresh_active and fresh_active != parent_id:
                fresh_parent = db.execute("select * from branch_commits where id = %s and save_id = %s", (fresh_active, save_id)).fetchone()
                if fresh_parent:
                    parent = fresh_parent
                    parent_id = fresh_active
            row = _insert_commit(
                db,
                save_id=save_id,
                parent_id=parent_id,
                turn_index=turn,
                kind="round",
                title=f"第 {turn} 回合",
                message=summary,
                summary=summary,
                content_preview=preview,
                state_path=snapshot_path,
                state_snapshot=data,
                player_input=player_input,
                gm_output=gm_response,
                metadata={"source": "runtime", "parent_commit_id": parent_id, "nonce": secrets.token_hex(8)},
            )
            _upsert_ref_by_id(db, ref_id, row["id"], active=True)
            _set_save_active(db, save_id, row["id"], ref_id)
            _write_checkout(db, int(save["user_id"]), save_id, ref_id, row["id"])
            # Q KB-backed 存储集成(每用户特性 kb_state,默认开):把本回合 state 完整拆进 KB 行
            # (COW,born=新 commit row["id"]),让存档状态 DB-resident、单一来源。同事务写;失败不破回合。
            from core.feature_flags import feature_enabled as _feat
            # kb_native 档(新档,创建即 seed)始终落 KB;旧档按每用户 kb_state 开关。
            if bool(save.get("kb_native")) or _feat("kb_state", int(save["user_id"]) if save.get("user_id") is not None else None):
                try:
                    from kb.save_kb import import_state as _kb_import, maintain_structured_kb as _kb_maintain
                    _kb_import(db, save_id, int(row["id"]), data)
                    # 史官:从本回合正文确定性维护结构化 KB(实体 encountered + 全部关系)
                    _sid = (save or {}).get("script_id")
                    if _sid:
                        _kb_maintain(db, save_id, int(_sid), int(row["id"]), gm_response or "",
                                     player_name=str(((data or {}).get("player") or {}).get("name") or ""))
                except Exception as _kbe:
                    import logging as _lg
                    _lg.getLogger("kb_state").warning("[kb_state] persist import/maintain skip: %s", _kbe)
    if missing_parent:
        if _depth >= 2:
            return {"ok": False, "reason": "runtime 指向的父节点不存在(重绑后仍缺失)"}
        rebound = bootstrap_runtime_binding(user_id=user_id)
        if rebound and rebound.get("active_commit_id") != parent_id:
            return record_runtime_turn(player_input, gm_response, runtime_state_path, user_id=user_id, _depth=_depth + 1)
        return {"ok": False, "reason": "runtime 指向的父节点不存在"}
    effective_user_id = user_id or int(save.get("user_id") or 0)
    runtime_info = _runtime_module.update_active_node(
        row["id"], snapshot_path, ref_id=ref_id, user_id=effective_user_id,
    )
    schedule_llm_summary(int(row["id"]), player_input, gm_response)
    return {"ok": True, "node": expose(row), "runtime": runtime_info}


def persist_runtime_state(
    runtime_state_path: str | None = None,
    user_id: int | None = None,
    state_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the mutable game worktree without creating a new commit."""
    meta = _runtime_module.read_runtime(user_id=user_id) or bootstrap_runtime_binding(user_id=user_id)
    if user_id and int(meta.get("user_id") or 0) not in {0, int(user_id)}:
        meta = bootstrap_runtime_binding(user_id=user_id)
    if not meta:
        return {"ok": False, "reason": "未激活存档 runtime"}

    save_id = int(meta.get("save_id") or 0)
    commit_id = int(meta.get("active_commit_id") or meta.get("active_branch_node_id") or 0)
    ref_id = int(meta.get("active_ref_id") or 0) or None
    if not save_id or not commit_id:
        return {"ok": False, "reason": "runtime 缺少存档或节点"}

    from state import SAVE_FILE
    state_path = Path(runtime_state_path or meta.get("runtime_state_path") or SAVE_FILE)
    state_data = json.loads(json.dumps(state_data, ensure_ascii=False)) if isinstance(state_data, dict) else load_state(state_path)
    init_db()
    with connect() as db:
        # 与 record_runtime_turn 同 key 的事务级 advisory lock:串行化 autosave 与回合提交。
        # 否则二者并发(多 tab:一 tab 发回合创建 commit N+1,另一 tab 改 state 触发 autosave)时,
        # autosave 可能在回合提交后用过时 commit_id 回退活跃指针 + 覆盖刚提交回合 → 丢回合。
        # 持锁后回合无法在本函数读 save 与写 UPDATE 之间提交,save.active 在事务内稳定。
        acquire_save_advisory_lock(db, save_id, user_id)
        save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
        if user_id and (not save or int(save["user_id"]) != int(user_id)):
            return {"ok": False, "reason": "runtime 不属于当前用户"}
        if not save:
            return {"ok": False, "reason": "存档不存在"}
        db_snapshot = commit_state(save)
        # 防丢回合:以事务内 game_saves 的当前活跃指针为权威(而非可能滞后的 meta.commit_id —
        # record_runtime_turn 的 TXN1 先更 game_saves,runtime 表由其后的 update_active_node 异步同步,
        # 故回合后正常即存在 game_saves=N+1 / runtime 表=N 的瞬时分歧)。二者分歧说明本 checkpoint
        # 的 state_data 基于过时 commit:此时不回退指针、也不用过时 state 覆盖 state_snapshot,
        # 改用 DB 当前真相(指针与快照保持不变),dirty 态留待下次 checkpoint 重存。
        db_active = int(save.get("active_commit_id") or save.get("active_branch_node_id") or 0)
        if db_active and commit_id and db_active != commit_id:
            commit_id = db_active
            ref_id = int(save.get("active_branch_ref_id") or 0) or ref_id
            state_data = db_snapshot
            state_path = Path(save.get("state_path") or state_path)
        elif _snapshot_quality(state_data) + 5 < _snapshot_quality(db_snapshot):
            state_data = db_snapshot
            state_path = Path(save.get("state_path") or state_path)
        # 同步酒馆角色/persona 卡列与 state JSON 对齐(LLM 工具只 mutate JSON,不写列;
        # 单写者落库时顺带把列追平,根治走列读卡的 404)。COALESCE 保护:只在 snapshot 有
        # 有效卡 id 时覆盖,缺失/非酒馆存档保留旧列,绝不清成 NULL。
        _tav_char, _tav_persona = tavern_card_cols(state_data)
        db.execute(
            """
            update game_saves
            set state_snapshot = %s,
                active_commit_id = %s,
                active_branch_node_id = %s,
                active_branch_ref_id = %s,
                tavern_character_card_id = coalesce(%s, tavern_character_card_id),
                tavern_persona_card_id = coalesce(%s, tavern_persona_card_id),
                row_version = row_version + 1,
                updated_at = now()
            where id = %s
            """,
            (Jsonb(state_data), commit_id, commit_id, ref_id, _tav_char, _tav_persona, save_id),
        )
        snap_hash = _state_snapshot_hash(state_data)
        turn = int(state_data.get("turn", 0)) if isinstance(state_data, dict) else 0
        db.execute(
            """
            insert into runtime_checkouts(user_id, save_id, ref_id, commit_id, runtime_state_path, state_snapshot,
                                           snapshot_hash, dirty, turn_at_commit, turn_runtime)
            values (%s, %s, %s, %s, %s, %s, %s, false, %s, %s)
            on conflict(user_id, save_id) do update
              set ref_id = excluded.ref_id,
                  commit_id = excluded.commit_id,
                  runtime_state_path = excluded.runtime_state_path,
                  state_snapshot = excluded.state_snapshot,
                  snapshot_hash = excluded.snapshot_hash,
                  dirty = false,
                  turn_at_commit = excluded.turn_at_commit,
                  turn_runtime = excluded.turn_runtime,
                  row_version = runtime_checkouts.row_version + 1,
                  updated_at = now()
            """,
            (int(save["user_id"]), save_id, ref_id, commit_id, str(state_path), Jsonb(state_data),
             snap_hash, turn, turn),
        )
    runtime_info = _runtime_module.write_runtime(int(save["user_id"]), save_id, commit_id, str(state_path), ref_id=ref_id)
    runtime_info["commit_id"] = commit_id
    runtime_info["dirty"] = False
    return {"ok": True, "runtime": runtime_info, "commit_id": commit_id}


def bootstrap_runtime_binding(user_id: int | None = None) -> dict[str, Any]:
    init_db()
    seed_request: tuple[int, int, str] | None = None
    with connect() as db:
        if user_id:
            save = db.execute(
                """
                select game_saves.*, users.id as owner_id
                from game_saves join users on users.id = game_saves.user_id
                where users.id = %s
                order by game_saves.updated_at desc, game_saves.id desc
                limit 1
                """,
                (user_id,),
            ).fetchone()
        else:
            save = db.execute(
                """
                select game_saves.*, users.id as owner_id
                from game_saves join users on users.id = game_saves.user_id
                order by game_saves.updated_at desc, game_saves.id desc
                limit 1
                """
            ).fetchone()
        if not save:
            return {}
        commit = None
        commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
        if commit_id:
            commit = db.execute("select * from branch_commits where id = %s and save_id = %s", (commit_id, save["id"])).fetchone()
        if not commit:
            commit = db.execute(
                "select * from branch_commits where save_id = %s order by id desc limit 1",
                (save["id"],),
            ).fetchone()
        if not commit:
            from state import SAVE_FILE
            seed_path = save.get("state_path") or str(SAVE_FILE)
            owner_id = save["owner_id"]
            save_id = save["id"]
            seed_request = (owner_id, save_id, seed_path)
            ref = None
        else:
            ref = db.execute(
                "select * from branch_refs where save_id = %s and is_active = true and target_commit_id = %s order by id desc limit 1",
                (save["id"], commit["id"]),
            ).fetchone()
            if not ref:
                ref = _find_or_create_ref_for_commit(db, int(save["owner_id"]), commit)  # type: ignore[assignment]
            _set_save_active(db, save["id"], commit["id"], ref["id"])
            _write_checkout(db, int(save["owner_id"]), save["id"], ref["id"], commit["id"])
    if seed_request:
        owner_id, save_id, seed_path = seed_request
        from platform_app.branches.seed import _seed_and_bootstrap
        return _seed_and_bootstrap(owner_id, save_id, seed_path, user_id=user_id)
    return _runtime_module.activate_state_snapshot(save["owner_id"], save["id"], commit["id"], commit_state(commit), commit["state_path"], ref_id=ref["id"])


def mark_runtime_dirty(save_id: int, runtime_state: dict[str, Any]) -> None:
    """Runtime state 已被改写、但尚未 commit 时调用。"""
    snap_hash = _state_snapshot_hash(runtime_state)
    turn = int(runtime_state.get("turn", 0)) if isinstance(runtime_state, dict) else 0
    with connect() as db:
        _db_mark_checkout_dirty(db, save_id, runtime_state, snap_hash, turn)
