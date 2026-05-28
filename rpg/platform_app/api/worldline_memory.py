"""platform_app.api.worldline_memory — /api/worldline/variables, /api/memories 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import knowledge
from ._deps import _resolve_save_id, json_response, require_user

router = APIRouter()


# worldline variable 写入路由：见 ui.py（同时更新 runtime state 和 DB）
# 此处提供只读列表接口供前端管理面板使用
# 保留 request：需要读 request.query_params.get("save_id")
@router.get("/api/worldline/variables")
async def api_worldline_variables(request: Request, user=Depends(require_user)):
    body = {"save_id": request.query_params.get("save_id")}
    try:
        save_id = _resolve_save_id(user["id"], body)
        return json_response({"ok": True, **knowledge.list_worldline_variables(user["id"], save_id)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# 保留 request：需要读 request.query_params（save_id/bucket/limit/cursor）
@router.get("/api/memories")
async def api_memories(request: Request, user=Depends(require_user)):
    body = {"save_id": request.query_params.get("save_id")}
    try:
        save_id = _resolve_save_id(user["id"], body)
        return json_response({
            "ok": True,
            **knowledge.list_memories(
                user["id"],
                save_id,
                bucket=request.query_params.get("bucket"),
                limit=request.query_params.get("limit"),
                cursor=request.query_params.get("cursor"),
            ),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
