"""platform_app.api.scripts.worldbook_canon —— 世界书 + canon 实体只读端点。

worldbook 列表、canon 实体列表/详情(MD 编辑器按类型拉取)。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Query

from ... import knowledge
from ...db import connect
from .._deps import json_response, require_user, value_error_response
from ._shared import router


@router.get("/api/scripts/{script_id}/worldbook")
async def api_script_worldbook(script_id: int, limit: int | None = None, cursor: str | None = None, fetch_all: bool = False, user=Depends(require_user)):
    # fetch_all=true:编辑器一次性全量加载(绕开游标分页漏条);否则走默认游标分页。
    try:
        return json_response({"ok": True, **knowledge.list_worldbook_entries(
            user["id"], script_id, limit, cursor, fetch_all=fetch_all)})
    except ValueError as exc:
        return value_error_response(exc)


# canon 实体列表(MD 编辑器按类型拉取)。鉴权 owner 或 subscriber(只读),与 GET worldbook
# 的访问模型一致;分页/返回沿用 page_payload(items + page.{limit,next_cursor,has_more})。
_CANON_LIST_COLS = (
    "id, logical_key, name, full_name, type, entity_subtype, parent_logical_key, "
    "summary, identity, background, aliases, attrs, "
    "first_revealed_chapter, public_knowledge, importance, created_at"
)


@router.get("/api/scripts/{script_id}/canon-entities")
async def api_script_canon_entities(
    script_id: int, limit: int | None = None, cursor: str | None = None,
    fetch_all: bool = False, entity_type: str | None = Query(None, alias="type"),
    user=Depends(require_user),
):
    """列出 canon 实体全字段,供 MD 编辑器 / 剧本详情表格拉取。owner 或 subscriber 可读。

    fetch_all=true:编辑器一次性全量(同 GET worldbook)。默认分页按 importance 排序、
    游标却按 id 截断,第 2 页起会漏条;且默认一页 50 条 —— 新建的实体 importance=0 排在
    最后,资源管理器里「建了看不到」。type=按类型过滤(表格顶部的类型切换一直在传,之前被忽略)。
    """
    from kb.canon_repo import normalize_canon_type

    from ...db import cursor_id, limit_value, page_payload
    page_limit = limit_value(limit)
    before_id = cursor_id(cursor)
    raw_type = (entity_type or "").strip()
    type_filter = (normalize_canon_type(raw_type) or raw_type) if raw_type else None
    with connect() as db:
        owned = db.execute(
            """select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        if fetch_all:
            rows = db.execute(
                f"""
                select {_CANON_LIST_COLS} from kb_canon_entities
                where script_id = %s and (%s::text is null or type = %s)
                order by importance desc, id desc
                """,
                (script_id, type_filter, type_filter),
            ).fetchall()
            return json_response({"ok": True, **page_payload([dict(r) for r in rows], len(rows))})
        rows = db.execute(
            f"""
            select {_CANON_LIST_COLS} from kb_canon_entities
            where script_id = %s and (%s::bigint is null or id < %s)
              and (%s::text is null or type = %s)
            order by importance desc, id desc
            limit %s
            """,
            (script_id, before_id, before_id, type_filter, type_filter, page_limit + 1),
        ).fetchall()
    return json_response({"ok": True, **page_payload([dict(r) for r in rows], page_limit)})


@router.get("/api/scripts/{script_id}/canon-entities/{logical_key}")
async def api_script_canon_entity(script_id: int, logical_key: str, user=Depends(require_user)):
    """单个 canon 实体全字段。owner 或 subscriber 可读。"""
    with connect() as db:
        owned = db.execute(
            """select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        row = db.execute(
            f"select {_CANON_LIST_COLS} from kb_canon_entities where script_id = %s and logical_key = %s",
            (script_id, logical_key),
        ).fetchone()
    if not row:
        return json_response({"ok": False, "error": "这个设定实体不存在(可能已被删除),刷新列表后再试"}, status_code=404)
    return json_response({"ok": True, "entity": dict(row)})
