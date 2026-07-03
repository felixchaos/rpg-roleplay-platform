"""worldbook_overlay.py — 存档级世界书 overlay 管理路由 (/api/worldbook/overlay)。

酒馆存档(script_id NULL)没有剧本 worldbook_entries,世界书全靠 save_worldbook_overlays
的 addition 条目。此前只有 LLM/命令工具能加(command_tools_worldbook),前端无入口(反馈#93)。
这里补一组 UI 端点:list(全文)/ add(复用 worldbook_add 工具,ui_button origin)/ remove(直删 addition)。

与 /set(routes/worldline.py)同款:走当前活跃存档 + 归属校验;overlay 直接落 save_worldbook_overlays
表(不进 state.data),GM 检索侧(worldbook provider / worldbook_agent)直读该表,加完即生效。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_user

router = APIRouter()


def _uid(api_user: dict[str, Any] | None) -> int:
    return int(api_user.get("id")) if api_user and api_user.get("id") else 0


@router.get("/api/worldbook/overlay")
async def api_worldbook_overlay_list(api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """列出当前活跃存档的世界书 overlay(additions 全文 + retirements),供前端管理面板。"""
    from app import _resolve_persist_target
    from platform_app.db import connect, init_db

    _pu, save_id = _resolve_persist_target(api_user)
    if not save_id:
        return JSONResponse({"ok": True, "additions": [], "retirements": []})
    uid = _uid(api_user)
    init_db()
    with connect() as db:
        own = db.execute("select id from game_saves where id=%s and user_id=%s", (save_id, uid)).fetchone()
        if not own:
            return JSONResponse({"ok": False, "error": "无权访问该存档"}, status_code=403)
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
    return JSONResponse({"ok": True, "save_id": int(save_id), "additions": additions, "retirements": retirements})


@router.post("/api/worldbook/overlay")
async def api_worldbook_overlay_add(request: Request, api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """新增一条世界书 addition —— 走 dispatcher 的 worldbook_add(ui_button origin,归属由工具保证)。"""
    from app import _ensure_loaded, _resolve_persist_target
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool

    _pu, save_id = _resolve_persist_target(api_user)
    if not save_id:
        return JSONResponse({"ok": False, "error": "无活跃存档"}, status_code=400)
    body = await request.json()
    title = str((body or {}).get("title") or "").strip()
    content = str((body or {}).get("content") or "").strip()
    if not title or not content:
        return JSONResponse({"ok": False, "error": "标题和正文不能为空"}, status_code=400)
    keys = (body or {}).get("keys") or []
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    elif isinstance(keys, list):
        keys = [str(k).strip() for k in keys if str(k).strip()]
    else:
        keys = []
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
        return JSONResponse({"ok": False, "error": getattr(result, "error", None) or "新增失败"}, status_code=400)
    return JSONResponse({"ok": True, "message": getattr(result, "result", "已新增")})


@router.post("/api/worldbook/overlay/remove")
async def api_worldbook_overlay_remove(request: Request, api_user: dict[str, Any] = Depends(get_current_user)) -> JSONResponse:
    """删除一条 addition overlay(归属校验:overlay 所属 save 属于当前用户)。retirement 不在此删。"""
    from platform_app.db import connect, init_db

    body = await request.json()
    try:
        oid = int((body or {}).get("id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "id 无效"}, status_code=400)
    uid = _uid(api_user)
    init_db()
    with connect() as db:
        row = db.execute(
            "select o.id from save_worldbook_overlays o join game_saves g on g.id=o.save_id "
            "where o.id=%s and g.user_id=%s and o.kind='addition'",
            (oid, uid),
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "条目不存在或无权删除"}, status_code=404)
        db.execute("delete from save_worldbook_overlays where id=%s", (oid,))
    return JSONResponse({"ok": True, "removed": oid})
