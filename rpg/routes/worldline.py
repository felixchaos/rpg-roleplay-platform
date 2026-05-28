"""worldline.py — 世界线变量管理路由 (/api/worldline/*)。"""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/worldline/variable")
async def api_worldline_variable(request: Request) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher 的 set_user_variable 工具。"""
    from app import (
        _require_api_user, _payload, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint,
    )
    from platform_app import knowledge as platform_knowledge
    api_user = _require_api_user(request)
    body = await request.json()
    key = body.get("key", "")
    value = body.get("value", "")
    state = _ensure_loaded(api_user)
    persist_user_id, active_save_id = _resolve_persist_target(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="set_user_variable",
        args={"key": key, "value": value},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=active_save_id or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    # 同步写入 DB(保证前端管理面板可见)
    if persist_user_id and active_save_id:
        try:
            platform_knowledge.set_worldline_variable(persist_user_id, active_save_id, key, value, source="user")
        except Exception:
            pass
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@router.post("/api/worldline/variable/remove")
async def api_worldline_variable_remove(request: Request) -> JSONResponse:
    """task 87 Phase 6: destructive,走 dispatcher remove_user_variable 工具。"""
    from app import (
        _require_api_user, _payload, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint,
    )
    from platform_app import knowledge as platform_knowledge
    api_user = _require_api_user(request)
    body = await request.json()
    key = body.get("key", "")
    state = _ensure_loaded(api_user)
    persist_user_id, active_save_id = _resolve_persist_target(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="remove_user_variable",
        args={"key": key},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=active_save_id or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    if persist_user_id and active_save_id:
        try:
            platform_knowledge.remove_worldline_variable(persist_user_id, active_save_id, key)
        except Exception:
            pass
    return JSONResponse({"ok": True, "state": _payload(api_user)})
