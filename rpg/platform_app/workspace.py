from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from state import SAVE_FILE

from . import branches, runtime
from .db import connect, cursor_id, expose, init_db, limit_value, page_payload, status as db_status
from .security import public_user


BASE_TITLE = "《我蕾穆丽娜不爱你》"


def ensure_default(user_id: int) -> None:
    init_db()
    with connect() as db:
        script = db.execute("select * from scripts where owner_id = %s order by id limit 1", (user_id,)).fetchone()
        if not script:
            script = db.execute(
                """
                insert into scripts(owner_id, title, description, source_path)
                values (%s, %s, %s, %s)
                returning *
                """,
                (user_id, BASE_TITLE, "柏林 RPG 默认剧本", "rpg/indexes"),
            ).fetchone()
        save = db.execute(
            "select * from game_saves where user_id = %s and script_id = %s order by id limit 1",
            (user_id, script["id"]),
        ).fetchone()
        if not save:
            save = db.execute(
                """
                insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
                values (%s, %s, %s, %s, %s)
                returning *
                """,
                (user_id, script["id"], "当前自动存档", str(SAVE_FILE), Jsonb(_read_state_snapshot())),
            ).fetchone()
    branches.seed_tree(save["id"], str(SAVE_FILE))
    if not runtime.read_runtime(user_id=user_id):
        with connect() as db:
            active = db.execute("select active_branch_node_id from game_saves where id = %s", (save["id"],)).fetchone()
            node_id = active.get("active_branch_node_id") if active else None
        if node_id:
            branches.activate_node(user_id, int(node_id))


def overview(user: dict | None) -> dict[str, Any]:
    if not user:
        return {"user": None, "auth_required": True, "database": db_status()}
    ensure_default(user["id"])
    with connect() as db:
        scripts = db.execute("select * from scripts where owner_id = %s order by updated_at desc, id desc limit 50", (user["id"],)).fetchall()
        saves = db.execute("select * from game_saves where user_id = %s order by updated_at desc, id desc limit 50", (user["id"],)).fetchall()
        settings = db.execute("select key, value from settings where user_id = %s", (user["id"],)).fetchall()
        branch_counts = {
            row["save_id"]: row["count"]
            for row in db.execute(
                """
                select n.save_id,
                       sum(
                         case
                           when n.kind = 'gm' and exists (
                             select 1 from branch_commits p
                             where p.id = n.parent_id
                               and p.kind = 'player'
                               and p.turn_index = n.turn_index
                           ) then 0
                           else 1
                         end
                       )::int as count
                from branch_commits n
                where n.save_id in (select id from game_saves where user_id = %s)
                group by n.save_id
                """,
                (user["id"],),
            ).fetchall()
        }
        assets = db.execute("select * from assets where user_id = %s order by id desc limit 20", (user["id"],)).fetchall()
    return {
        "user": public_user(user),
        "database": db_status(),
        "scripts": [expose(row) for row in scripts],
        "saves": [{**expose(row), "branch_count": branch_counts.get(row["id"], 0)} for row in saves],
        "settings": {row["key"]: row["value"] for row in settings},
        "assets": [expose(row) for row in assets],
        "runtime": runtime.read_runtime(user_id=user["id"]),
    }


def create_save(user_id: int, script_id: int, title: str) -> dict[str, Any]:
    init_db()
    with connect() as db:
        script = db.execute("select * from scripts where id = %s and owner_id = %s", (script_id, user_id)).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        save = db.execute(
            """
            insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
            values (%s, %s, %s, %s, %s)
            returning *
            """,
            (user_id, script_id, title.strip() or "新存档", str(SAVE_FILE), Jsonb(_read_state_snapshot())),
        ).fetchone()
    branches.seed_tree(save["id"], str(SAVE_FILE))
    return expose(save)


def scripts(user_id: int) -> list[dict[str, Any]]:
    ensure_default(user_id)
    with connect() as db:
        return [expose(row) for row in db.execute("select * from scripts where owner_id = %s order by updated_at desc, id desc limit 200", (user_id,)).fetchall()]


def scripts_page(user_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    ensure_default(user_id)
    page_limit = limit_value(limit)
    before_id = cursor_id(cursor)
    with connect() as db:
        rows = db.execute(
            """
            select * from scripts
            where owner_id = %s and (%s::bigint is null or id < %s)
            order by id desc
            limit %s
            """,
            (user_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


def _read_state_snapshot() -> dict[str, Any]:
    """新存档的初始 state。

    安全：绝对不能读全局 SAVE_FILE（那是 admin 的运行态，会泄露给新用户）。
    走 state.GameState.new()，得到干净的初始 state。
    """
    try:
        from state import GameState
        return GameState.new().data
    except Exception:
        return {"history": [], "turn": 0}


# 列表页只取摘要字段；完整 state_snapshot 通过 save_detail() 单独取
_SAVE_LIST_COLUMNS = """
    id, public_id, user_id, script_id, title, state_path,
    active_commit_id, active_branch_node_id, active_branch_ref_id,
    created_at, updated_at, row_version,
    (state_snapshot->>'turn')::int as turn,
    (state_snapshot->'player'->>'name') as player_name,
    coalesce(jsonb_array_length(state_snapshot->'history'), 0) as history_count,
    coalesce((state_snapshot->'world'->>'time'), '') as world_time
"""


def saves(user_id: int) -> list[dict[str, Any]]:
    ensure_default(user_id)
    with connect() as db:
        return [expose(row) for row in db.execute(
            f"select {_SAVE_LIST_COLUMNS} from game_saves where user_id = %s order by updated_at desc, id desc limit 200",
            (user_id,),
        ).fetchall()]


def saves_page(user_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    ensure_default(user_id)
    page_limit = limit_value(limit)
    before_id = cursor_id(cursor)
    with connect() as db:
        rows = db.execute(
            f"""
            select {_SAVE_LIST_COLUMNS} from game_saves
            where user_id = %s and (%s::bigint is null or id < %s)
            order by id desc
            limit %s
            """,
            (user_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


def save_detail(user_id: int, save_id: int) -> dict[str, Any]:
    """单条详情：包含完整 state_snapshot。前端只在打开 save 时才调。"""
    with connect() as db:
        row = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError(f"无权访问该存档: {save_id}")
    return expose(row) or {}
