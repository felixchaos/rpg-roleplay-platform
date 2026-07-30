"""worldbook_overlay.py — 存档级世界书 overlay 管理路由 (/api/worldbook/overlay)。

酒馆存档(script_id NULL)没有剧本 worldbook_entries,世界书全靠 save_worldbook_overlays
的 addition 条目。此前只有 LLM/命令工具能加(command_tools_worldbook),前端无入口(反馈#93)。
这里补一组 UI 端点:list(全文)/ add(复用 worldbook_add 工具,ui_button origin)/ update(改
已有 addition)/ remove(直删 addition)。

update 的来由:面板长期只有「+」和「×」——写错一个字只能删掉重打,而 overlay 正文往往是
几百字的功法/设定长文(用户反馈 07-30)。改 = 直接 update 该行,不新增 overlay 版本:
overlay 表不是 COW 表(无 born/retired commit),检索侧每回合直读,改完即生效。

与 /set(routes/worldline.py)同款:走当前活跃存档 + 归属校验;overlay 直接落 save_worldbook_overlays
表(不进 state.data),GM 检索侧(worldbook provider / worldbook_agent)直读该表,加完即生效。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from platform_app.api._deps import json_response

from routes._deps_fastapi import get_current_user
from routes._deps_fastapi import _uid_or_zero as _uid

router = APIRouter()


def _norm_keys(raw: Any) -> list[str]:
    """触发关键词归一:接受逗号串或列表,去空白去空项。add / update 共用一条缝。"""
    if isinstance(raw, str):
        return [k.strip() for k in raw.split(",") if k.strip()]
    if isinstance(raw, list):
        return [str(k).strip() for k in raw if str(k).strip()]
    return []


def _own_addition(db, oid: int, uid: int) -> bool:
    """该 overlay 存在、是 addition、且所属存档属于 uid。update / remove 共用。

    归属判断一律走 `perms.owns_save`(CLAUDE.md:严禁手写归属 SQL)——本文件早先
    三处各写了一遍 game_saves join,已在此收口。retirement 不经这里(无可编辑字段)。
    """
    from platform_app.perms import owns_save

    row = db.execute(
        "select save_id, kind from save_worldbook_overlays where id=%s", (oid,)
    ).fetchone()
    if not row:
        return False
    row = dict(row)
    if row.get("kind") != "addition":
        return False
    return owns_save(db, int(row["save_id"]), uid)


@router.get("/api/worldbook/overlay")
async def api_worldbook_overlay_list(api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """列出当前活跃存档的世界书 overlay(additions 全文 + retirements),供前端管理面板。"""
    from app import _resolve_persist_target
    from platform_app.db import connect, init_db

    from platform_app.perms import owns_save

    _pu, save_id = _resolve_persist_target(api_user)
    if not save_id:
        return json_response({"ok": True, "additions": [], "retirements": []})
    uid = _uid(api_user)
    init_db()
    with connect() as db:
        if not owns_save(db, int(save_id), uid):
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
        rows = db.execute(
            "select id, kind, title, content, keys, priority, retired_entry_id, retired_reason, introduced_turn "
            "from save_worldbook_overlays where save_id=%s order by id asc",
            (save_id,),
        ).fetchall() or []
    additions, retirements = [], []
    for r in rows:
        r = dict(r)
        if r["kind"] == "addition":
            additions.append({
                "id": r["id"], "title": r["title"], "content": r["content"] or "",
                "keys": r["keys"] or [], "priority": r["priority"], "introduced_turn": r["introduced_turn"],
            })
        elif r["kind"] == "retirement":
            retirements.append({
                "id": r["id"], "retired_entry_id": r["retired_entry_id"],
                "retired_reason": r["retired_reason"], "introduced_turn": r["introduced_turn"],
            })
    return json_response({"ok": True, "save_id": int(save_id), "additions": additions, "retirements": retirements})


@router.post("/api/worldbook/overlay")
async def api_worldbook_overlay_add(request: Request, api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """新增一条世界书 addition —— 走 dispatcher 的 worldbook_add(ui_button origin,归属由工具保证)。"""
    from app import _ensure_loaded, _resolve_persist_target
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool

    _pu, save_id = _resolve_persist_target(api_user)
    if not save_id:
        return json_response({"ok": False, "error": "无活跃存档"}, status_code=400)
    body = await request.json()
    title = str((body or {}).get("title") or "").strip()
    content = str((body or {}).get("content") or "").strip()
    if not title or not content:
        return json_response({"ok": False, "error": "标题和正文不能为空"}, status_code=400)
    keys = _norm_keys((body or {}).get("keys"))
    try:
        priority = int((body or {}).get("priority") or 50)
    except (TypeError, ValueError):
        priority = 50
    state = _ensure_loaded(api_user)
    result = dispatch_ui_tool(
        tool_name="worldbook_add",
        args={"save_id": int(save_id), "title": title, "content": content, "keys": keys, "priority": priority},
        user_id=_uid(api_user), save_id=int(save_id), state=state,
    )
    if not getattr(result, "ok", False):
        return json_response({"ok": False, "error": getattr(result, "error", None) or "新增失败"}, status_code=400)
    return json_response({"ok": True, "message": getattr(result, "result", "已新增")})


@router.post("/api/worldbook/overlay/update")
async def api_worldbook_overlay_update(request: Request, api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """改一条已有 addition 的 标题 / 正文 / 关键词 / 优先级。

    **部分更新**:只写 body 里真正出现的键(`"title" in body`,不是 truthy 判断)——
    面板将来只想改优先级时不该被迫回传整段正文。title/content 若出现则不许为空:
    建表 check 约束要求 addition 的 title <> '',空正文的条目在检索侧等于噪声。
    """
    from psycopg.types.json import Jsonb

    from platform_app.db import connect, init_db

    body = await request.json() or {}
    try:
        oid = int(body.get("id"))
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "id 无效"}, status_code=400)

    sets: list[str] = []
    params: list[Any] = []
    for field in ("title", "content"):
        if field in body:
            val = str(body.get(field) or "").strip()
            if not val:
                label = "标题" if field == "title" else "正文"
                return json_response({"ok": False, "error": f"{label}不能为空"}, status_code=400)
            sets.append(f"{field}=%s")
            params.append(val)
    if "keys" in body:
        sets.append("keys=%s")
        params.append(Jsonb(_norm_keys(body.get("keys"))))
    if "priority" in body:
        raw = body.get("priority")
        # 不用 `or 50`:优先级 0 是合法值(压到最低),`0 or 50` 会把它偷偷改成 50。
        try:
            priority = 50 if raw in (None, "") else int(raw)
        except (TypeError, ValueError):
            priority = 50
        sets.append("priority=%s")
        params.append(priority)
    if not sets:
        return json_response({"ok": False, "error": "没有要修改的字段"}, status_code=400)

    uid = _uid(api_user)
    init_db()
    with connect() as db:
        if not _own_addition(db, oid, uid):
            return json_response({"ok": False, "error": "条目不存在或无权修改"}, status_code=404)
        db.execute(
            f"update save_worldbook_overlays set {', '.join(sets)}, updated_at=now() where id=%s",  # noqa: S608 — set 片段全是字面量列名,值走参数
            (*params, oid),
        )
    return json_response({"ok": True, "updated": oid})


@router.post("/api/worldbook/overlay/remove")
async def api_worldbook_overlay_remove(request: Request, api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """删除一条 addition overlay(归属校验:overlay 所属 save 属于当前用户)。retirement 不在此删。"""
    from platform_app.db import connect, init_db

    body = await request.json()
    try:
        oid = int((body or {}).get("id"))
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "id 无效"}, status_code=400)
    uid = _uid(api_user)
    init_db()
    with connect() as db:
        if not _own_addition(db, oid, uid):
            return json_response({"ok": False, "error": "条目不存在或无权删除"}, status_code=404)
        db.execute("delete from save_worldbook_overlays where id=%s", (oid,))
    return json_response({"ok": True, "removed": oid})
