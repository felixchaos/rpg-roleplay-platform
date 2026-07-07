"""Branch ref management: upsert, find-or-create, checkout writes."""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from platform_app.branches._helpers import MAIN_REF, commit_state, tavern_card_cols
from platform_app.branches.commits import _state_snapshot_hash


def _upsert_ref(db, save_id: int, name: str, target_commit_id: int, *, active: bool, kind: str = "head") -> dict[str, Any]:
    if active:
        db.execute("update branch_refs set is_active = false where save_id = %s", (save_id,))
    return db.execute(
        """
        insert into branch_refs(save_id, name, kind, target_commit_id, is_active)
        values (%s, %s, %s, %s, %s)
        on conflict(save_id, name) do update
          set kind = excluded.kind,
              target_commit_id = excluded.target_commit_id,
              is_active = excluded.is_active,
              row_version = branch_refs.row_version + 1,
              updated_at = now()
        returning *
        """,
        (save_id, name, kind, target_commit_id, active),
    ).fetchone()


def _upsert_ref_by_id(db, ref_id: int, target_commit_id: int, *, active: bool) -> dict[str, Any]:
    ref = db.execute("select * from branch_refs where id = %s", (ref_id,)).fetchone()
    if not ref:
        raise ValueError("runtime 指向的分支引用不存在")
    if active:
        db.execute("update branch_refs set is_active = false where save_id = %s", (ref["save_id"],))
    return db.execute(
        """
        update branch_refs
        set target_commit_id = %s, is_active = %s, row_version = row_version + 1, updated_at = now()
        where id = %s
        returning *
        """,
        (target_commit_id, active, ref_id),
    ).fetchone()


def _find_or_create_ref_for_commit(db, user_id: int, commit: dict[str, Any]) -> dict[str, Any]:
    ref = db.execute(
        """
        select * from branch_refs
        where save_id = %s and target_commit_id = %s
        order by case when kind = 'head' then 0 else 1 end, id desc
        limit 1
        """,
        (commit["save_id"], commit["id"]),
    ).fetchone()
    if ref:
        return _upsert_ref(db, commit["save_id"], ref["name"], commit["id"], active=True, kind=ref["kind"])
    return _upsert_ref(
        db,
        commit["save_id"],
        f"refs/runtime/user-{user_id}",
        commit["id"],
        active=True,
        kind="runtime",
    )


def _ensure_active_ref(db, save_id: int) -> None:
    save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
    if not save:
        return
    commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
    commit = None
    if commit_id:
        commit = db.execute("select * from branch_commits where id = %s and save_id = %s", (commit_id, save_id)).fetchone()
    if not commit:
        commit = db.execute("select * from branch_commits where save_id = %s order by id desc limit 1", (save_id,)).fetchone()
    if not commit:
        return
    ref = db.execute(
        "select * from branch_refs where save_id = %s and is_active = true and target_commit_id = %s order by id desc limit 1",
        (save_id, commit["id"]),
    ).fetchone()
    if not ref:
        ref = _upsert_ref(db, save_id, MAIN_REF, commit["id"], active=True)
    _set_save_active(db, save_id, commit["id"], ref["id"])


def _set_save_active(db, save_id: int, commit_id: int, ref_id: int | None) -> None:
    commit = db.execute("select state_snapshot from branch_commits where id = %s and save_id = %s", (commit_id, save_id)).fetchone()
    state_snapshot = commit_state(commit or {})
    # 把酒馆角色/persona 卡列与 state JSON 对齐(回合提交/切换激活时);COALESCE 保证
    # 仅在 snapshot 含有效卡 id 时覆盖,非酒馆存档与缺失字段保留旧列,绝不清成 NULL。
    _tav_char, _tav_persona = tavern_card_cols(state_snapshot)
    db.execute(
        """
        update game_saves
        set active_branch_node_id = %s,
            active_commit_id = %s,
            active_branch_ref_id = %s,
            state_snapshot = %s,
            tavern_character_card_id = coalesce(%s, tavern_character_card_id),
            tavern_persona_card_id = coalesce(%s, tavern_persona_card_id),
            row_version = row_version + 1,
            updated_at = now()
        where id = %s
        """,
        (commit_id, commit_id, ref_id, Jsonb(state_snapshot), _tav_char, _tav_persona, save_id),
    )


def _write_checkout(db, user_id: int, save_id: int, ref_id: int | None, commit_id: int) -> None:
    from state import SAVE_FILE as _SAVE_FILE
    commit = db.execute("select id, state_snapshot, state_path from branch_commits where id = %s and save_id = %s", (commit_id, save_id)).fetchone()
    # 历史祖先裁剪(history_elide):被恢复的 commit 若处于裁剪态,从后代无损重建
    # 并 un-elide 回写——它即将成为活跃头(工作树/materialize/换稿的权威源)。
    from platform_app.branches.history_elide import hydrate_commit_state, unelide_commit
    _was_elided = isinstance((commit or {}).get("state_snapshot"), dict) and \
        "_history_elided" in (commit or {}).get("state_snapshot", {})
    state_snapshot = hydrate_commit_state(db, save_id, commit or {})
    if _was_elided:
        unelide_commit(db, save_id, commit_id, state_snapshot)
    snap_hash = _state_snapshot_hash(state_snapshot)
    turn_at_commit = int(state_snapshot.get("turn", 0)) if isinstance(state_snapshot, dict) else 0
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
        (user_id, save_id, ref_id, commit_id, str(_SAVE_FILE), Jsonb(state_snapshot), snap_hash, turn_at_commit, turn_at_commit),
    )
