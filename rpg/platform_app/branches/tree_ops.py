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


def message_row_by_index(db, save_id: int, message_index: int):
    """Return the visible chat message row at the frontend history index."""
    try:
        idx = int(message_index)
    except (TypeError, ValueError):
        return None
    if idx < 0:
        return None
    return db.execute(
        """
        select id, turn, role
        from messages
        where save_id = %s and role in ('user', 'assistant')
        order by created_at asc, id asc
        offset %s limit 1
        """,
        (save_id, idx),
    ).fetchone()


def resolve_commit_id_by_message(user_id: int, save_id: int, message_index: int) -> int | None:
    """task 38：把 frontend 的 chat history message index 映射到 branch_commits.id。"""
    init_db()
    try:
        msg_index = int(message_index)
    except (TypeError, ValueError):
        return None
    with connect() as db:
        owned = db.execute(
            "select 1 from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not owned:
            return None
        # 前端 history index → round commit。
        # 结构:前端 history[0] 是 GM 开场白(不落 messages 表),其后严格 [玩家,GM] 交替。
        # 故 history 索引 K 落在第 (K//2) 个 round 边界:
        #   K 偶(开场白 / GM 消息) → turn = K//2 的 round commit:保留到本轮(从此之后开新分支)
        #   K 奇(玩家消息)        → turn = K//2(=(K-1)//2):截到本轮之前(玩家想重输入这轮)
        # 旧实现用 message_row_by_index 读 messages 表(不含开场白)→ 比 history 少 1 位 →
        # 从最后一条玩家消息 fork 被解析成"下一条 GM / 本轮"→ 命中满历史 commit →
        # /api/state 返回的历史跟原来一样长 → 用户看着"只回填了输入框、历史没截断"(就是报的 bug)。
        # 全库 branch_commits 只有 root/round 两种 kind(无 player/gm),按 turn_index 取该 round commit 即可。
        if msg_index < 0:
            return None
        turn_index = msg_index // 2
        # 多分支修复:前端展示的是**当前活跃分支**的历史,故 message_index 对应的是活跃分支内
        # 某 turn 的 commit。原实现 `turn_index=%s order by id desc` 是全 save 选,玩家检出
        # 历史节点开过新分支后,同一 turn 在多条分支都有 commit,会命中 id 最大(常是另一条
        # 后建分支)的那个 → 从错误分支 fork,历史串档。改为从活跃 commit 沿 parent 链上溯,
        # 只在活跃分支血缘内定位目标 turn。
        active = db.execute(
            "select coalesce(active_commit_id, active_branch_node_id) as cid "
            "from game_saves where id = %s",
            (save_id,),
        ).fetchone()
        active_cid = int((active or {}).get("cid") or 0)
        if active_cid:
            row = db.execute(
                """
                with recursive lineage(id, parent_id, turn_index) as (
                    select id, parent_id, turn_index from branch_commits
                    where id = %s and save_id = %s
                    union all
                    select bc.id, bc.parent_id, bc.turn_index from branch_commits bc
                    join lineage l on bc.id = l.parent_id
                )
                select id from lineage where turn_index = %s order by id desc limit 1
                """,
                (active_cid, save_id, turn_index),
            ).fetchone()
            if row:
                return int(row["id"])
            # 活跃血缘里没有正好等于 target 的 turn(缺口)→ 取血缘内 <= target 的最近一个
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
                (active_cid, save_id, turn_index),
            ).fetchone()
            if row:
                return int(row["id"])
        # 无活跃指针(异常)→ 退回旧的全 save 行为,保证不返 None 阻断功能
        row = db.execute(
            """
            select id from branch_commits
            where save_id = %s and turn_index = %s
            order by id desc limit 1
            """,
            (save_id, turn_index),
        ).fetchone()
        if row:
            return int(row["id"])
        row = db.execute(
            """
            select id from branch_commits
            where save_id = %s and turn_index <= %s
            order by turn_index desc, id desc limit 1
            """,
            (save_id, turn_index),
        ).fetchone()
        return int(row["id"]) if row else None


def collect_ids(db, node_id: int) -> list[int]:
    # seen 防环:正常数据下 parent 图无环(id 单调 + parent 指更早 id),但损坏存档 /
    # legacy 迁移可能产生 parent 指向自身或后代的环 → 原 BFS 会无限循环,而 collect_ids 是
    # delete_subtree 删除集的唯一来源 → 整个删除 worker 永挂。加 seen 集合幂等截断,代价极小。
    seen: set[int] = {node_id}
    ids = [node_id]
    queue = [node_id]
    while queue:
        current = queue.pop(0)
        children = [row["id"] for row in db.execute("select id from branch_commits where parent_id = %s", (current,)).fetchall()]
        for c in children:
            if c in seen:
                continue
            seen.add(c)
            ids.append(c)
            queue.append(c)
    return ids


def round_start_node(db, node: dict[str, Any]) -> dict[str, Any]:
    if node.get("kind") != "gm" or not node.get("parent_id"):
        return node
    parent = db.execute("select * from branch_commits where id = %s", (node["parent_id"],)).fetchone()
    if parent and parent["kind"] == "player" and parent["save_id"] == node["save_id"] and parent["turn_index"] == node["turn_index"]:
        return {**parent, "user_id": node["user_id"]}
    return node
