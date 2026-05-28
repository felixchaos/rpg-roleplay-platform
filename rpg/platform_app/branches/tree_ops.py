"""Tree read operations: tree(), resolve_commit_id_by_message, collect_ids, round_start_node."""
from __future__ import annotations

from typing import Any

from platform_app.branches._helpers import display_nodes
from platform_app.branches.maintenance import ensure_summaries
from platform_app.branches.seed import seed_tree
from platform_app.db import connect, cursor_id, expose, init_db, limit_value


def tree(user_id: int, save_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit, default=1000, maximum=5000)
    after_id = cursor_id(cursor)
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        needs_seed = not db.execute("select 1 from branch_commits where save_id = %s limit 1", (save_id,)).fetchone()
    if needs_seed:
        seed_tree(save_id, save["state_path"])
    with connect() as db:
        ensure_summaries(db, save_id)
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        rows = db.execute(
            """
            select * from branch_commits
            where save_id = %s and (%s::bigint is null or id > %s)
            order by id
            limit %s
            """,
            (save_id, after_id, after_id, page_limit + 1),
        ).fetchall()
        visible_raw = rows[:page_limit]
        ref_rows = db.execute(
            "select name, target_commit_id, is_active from branch_refs where save_id = %s",
            (save_id,),
        ).fetchall()
    refs_by_commit: dict[int, list[str]] = {}
    active_ref_by_commit: set[int] = set()
    for ref in ref_rows:
        if ref.get("target_commit_id"):
            refs_by_commit.setdefault(ref["target_commit_id"], []).append(ref["name"])
            if ref.get("is_active"):
                active_ref_by_commit.add(ref["target_commit_id"])
    has_more = len(rows) > page_limit
    visible = display_nodes(visible_raw)
    active_commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
    try:
        from platform_app import runtime as _runtime_pkg
        _rt = _runtime_pkg.read_runtime(user_id=int(user_id)) or {}
        if int(_rt.get("save_id") or 0) == int(save_id):
            rt_commit = (
                _rt.get("active_commit_id")
                or _rt.get("active_branch_node_id")
            )
            if rt_commit:
                active_commit_id = int(rt_commit)
    except Exception:
        pass
    for row in visible:
        row["commit_id"] = row["id"]
        row["node_id"] = row["id"]
        row["ref_names"] = refs_by_commit.get(row["id"], [])
        row["is_active"] = row["id"] == active_commit_id or row["id"] in active_ref_by_commit
        if row.get("object_hash"):
            row["object_hash_short"] = row["object_hash"][:10]
    return {
        "save": expose(save),
        "nodes": [expose(row) for row in visible],
        "refs": [expose(row) for row in ref_rows],
        "active_commit_id": active_commit_id,
        "active_branch_node_id": active_commit_id,
        "page": {
            "limit": page_limit,
            "next_cursor": str(visible_raw[-1]["id"]) if has_more and visible_raw else None,
            "has_more": has_more,
        },
    }


def resolve_commit_id_by_message(user_id: int, save_id: int, message_index: int) -> int | None:
    """task 38：把 frontend 的 chat history message index 映射到 branch_commits.id。"""
    init_db()
    try:
        turn_index = int(message_index) // 2
    except (TypeError, ValueError):
        return None
    is_player = (int(message_index) % 2 == 0) if message_index is not None else False
    with connect() as db:
        owned = db.execute(
            "select 1 from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not owned:
            return None
        preferred_kind = "player" if is_player else "gm"
        row = db.execute(
            """
            select id, kind from branch_commits
            where save_id = %s and turn_index = %s and kind = %s
            order by id desc limit 1
            """,
            (save_id, turn_index, preferred_kind),
        ).fetchone()
        if row:
            return int(row["id"])
        row = db.execute(
            """
            select id from branch_commits
            where save_id = %s and turn_index = %s
            order by id desc limit 1
            """,
            (save_id, turn_index),
        ).fetchone()
        return int(row["id"]) if row else None


def collect_ids(db, node_id: int) -> list[int]:
    ids = [node_id]
    queue = [node_id]
    while queue:
        current = queue.pop(0)
        children = [row["id"] for row in db.execute("select id from branch_commits where parent_id = %s", (current,)).fetchall()]
        ids.extend(children)
        queue.extend(children)
    return ids


def round_start_node(db, node: dict[str, Any]) -> dict[str, Any]:
    if node.get("kind") != "gm" or not node.get("parent_id"):
        return node
    parent = db.execute("select * from branch_commits where id = %s", (node["parent_id"],)).fetchone()
    if parent and parent["kind"] == "player" and parent["save_id"] == node["save_id"] and parent["turn_index"] == node["turn_index"]:
        return {**parent, "user_id": node["user_id"]}
    return node
