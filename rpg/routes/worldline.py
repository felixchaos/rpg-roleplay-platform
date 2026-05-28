"""worldline.py — 世界线变量管理路由 (/api/worldline/*)。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, StateResponse
from schemas.worldline import WorldlineVariableRemoveRequest, WorldlineVariableRequest

router = APIRouter()


@router.post("/api/worldline/variable", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_worldline_variable(
    body: WorldlineVariableRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher 的 set_user_variable 工具。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    from platform_app import knowledge as platform_knowledge
    body_dict = body.model_dump(exclude_none=True)
    key = body_dict.get("key", "")
    value = body_dict.get("value", "")
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


@router.post("/api/worldline/variable/remove", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_worldline_variable_remove(
    body: WorldlineVariableRemoveRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 87 Phase 6: destructive,走 dispatcher remove_user_variable 工具。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    from platform_app import knowledge as platform_knowledge
    body_dict = body.model_dump(exclude_none=True)
    key = body_dict.get("key", "")
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
