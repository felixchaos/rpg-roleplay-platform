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
from platform_app.branches.tree_ops import collect_ids, round_start_node, tree
from platform_app.db import connect, expose, init_db


def _realign_after_state_rewind(db, save_id: int, reverted_state: dict[str, Any] | None) -> None:
    """软回退后把进度信号族(progress_chapter/user_progress_floor/save_anchor_states/
    frontier)对齐到回退后快照的时间线章(矩阵审计 M1/M2:回退只退 state_snapshot,
    下一回合 retrieval self-heal 用 stale occurred 锚点把进度顶回=回退被静默撤销)。
    快照无时间线章信号时不猜(宁漏勿误,保持旧行为)。同事务内调用,异常向外传播。"""
    tl = (((reverted_state or {}).get("world") or {}).get("timeline") or {})
    target = tl.get("anchor_chapter") or tl.get("chapter_min")
    try:
        target = int(target)
    except (TypeError, ValueError):
        return
    if target < 1:
        return
    from gm_serving.settings import realign_progress_signals
    realign_progress_signals(db, int(save_id), target)


def delete_subtree(user_id: int, node_id: int) -> dict[str, Any]:
    init_db()
    runtime_payload: dict[str, Any] | None = None
    activate: dict[str, Any] | None = None  # 推迟到锁/连接释放后再执行的 runtime 激活(防连接池死锁)
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
        ids = collect_ids(db, node["id"], save_id=node["save_id"])
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
            # STABILITY(2026-06-14 CF 524 真因):activate_state_snapshot 会**另开一个 DB 连接**。
            # 若在持有本事务连接 + advisory 锁时调用,高并发下「人人持 conn#1+锁、等 conn#2」会把
            # PgBouncer 连接池拖入死锁 → worker 事件循环冻结 → 全站不响应。改为捕获参数、推迟到
            # with 块外(锁与连接均已释放)再激活,与 rollback_to_message 的做法一致。
            activate = {
                "save_id": node["save_id"],
                "commit_id": fallback["id"],
                "state": commit_state(fallback),
                "state_path": fallback["state_path"],
                "ref_id": ref["id"],
            }
            # M2:活跃指针回退到 fallback 后,进度信号族对齐回退后快照(被删分支里
            # 标 occurred 的未来章锚点重锁,防剧透闸不再按被删分支的最远章放行)。
            _realign_after_state_rewind(db, node["save_id"], activate["state"])
        save_id = node["save_id"]
    for path in paths:
        _unlink_branch_state(path)
    if activate:
        runtime_payload = _runtime_module.activate_state_snapshot(
            user_id,
            activate["save_id"],
            activate["commit_id"],
            activate["state"],
            activate["state_path"],
            ref_id=activate["ref_id"],
        )
    result = tree(user_id, save_id)
    if runtime_payload:
        result["runtime"] = runtime_payload
    return result


def rollback_to_message(
    user_id: int,
    save_id: int,
    message_index: int,
) -> dict[str, Any]:
    """task 116c — 删除消息 N 及之后所有 → 软回滚到 turn (N//2) 的 round commit(与 fork 同口径)。"""
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

        current_commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")

        # 「删除消息 N 及之后」→ 软回滚到 frontend history 约定下应保留的 round commit。
        # 前端 history index 约定:history[0]=GM 开场白(不落 messages 表),其后 [玩家,GM] 交替。
        # **绝不**用 message_row_by_index(读 flat messages 表:含开场空 user 行 + 非分支隔离 → 与 blob
        # history 错位 ≥1 位),那正是群反馈「删除会多回退一个回合」的根因(fork 早改 N//2、delete 漏同步)。
        #
        # 奇偶要分开(回归修复 v1.32.4):被删消息要【连它一起删】,故保留点 = 它【所在回合的前一个边界】。
        #  · N 奇 = 玩家输入(turn (N+1)//2 的输入)→ 保留到 turn N//2(=该回合之前),删该回合输入及之后。
        #    对奇数 N,N//2 == (N-1)//2,与 fork 一致。
        #  · N 偶 = GM 回复(turn N//2 的回复)→ 若只退到 N//2 会把这条 GM 回复一起【保留】(它就在该 round
        #    commit 里)= 用户点「删除此 GM 回复」却没删掉(v1.30.1 引入的回归)。故偶数再退一格 N//2-1,
        #    把这条 GM 回复所在的整回合删掉。开场(N=0)等边界 clamp ≥0(保留开场,删其后)。
        target_turn = msg_index // 2
        if msg_index % 2 == 0:
            target_turn = max(0, target_turn - 1)
        deleted_turn = target_turn + 1
        target_message_role = "user" if msg_index % 2 == 1 else "assistant"

        # 沿**活跃 commit 血缘**定位 turn=target_turn 的 commit(多分支隔离,内联 resolve_commit_id_by_message
        # 同款递归查询——不可直接调用它:会在本 advisory 锁内嵌套开连接致连接池死锁,见 5f0319a73)。
        # 血缘里缺该 turn(缺口)则取 ≤target_turn 的最近一个;再不行回退 root。
        target_commit = None
        active_cid = int(current_commit_id or 0)
        if active_cid and target_turn >= 0:
            row = db.execute(
                """
                with recursive lineage(id, parent_id, turn_index) as (
                    select id, parent_id, turn_index from branch_commits
                    where id = %s and save_id = %s
                    union all
                    select bc.id, bc.parent_id, bc.turn_index from branch_commits bc
                    join lineage l on bc.id = l.parent_id
                )
                select id from lineage where turn_index <= %s order by turn_index desc, id desc limit 1
                """,
                (active_cid, save_id, target_turn),
            ).fetchone()
            if row:
                target_commit = db.execute(
                    "select * from branch_commits where id = %s", (row["id"],)
                ).fetchone()
        if not target_commit:
            target_commit = db.execute(
                "select * from branch_commits where save_id = %s and kind = 'root' order by id asc limit 1",
                (save_id,),
            ).fetchone()
        if not target_commit:
            raise ValueError(f"找不到 turn {target_turn} 的 commit,无法回滚")

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

        # messages 表为 kb_native 的衍生展示表(真相在目标 commit 的 state_snapshot blob),按 turn
        # 清理 ≥deleted_turn 即可;指针/状态已回到 target_commit,前端 materialize 读 blob 得到正确截断。
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
        # M1:回滚到目标消息后,进度信号族对齐目标快照的时间线章
        _realign_after_state_rewind(db, save_id, target_state)

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
        # M1:重演回退(rewind_last_round)后,进度信号族对齐父 commit 快照的时间线章
        _realign_after_state_rewind(db, save_id, reverted_state)

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
