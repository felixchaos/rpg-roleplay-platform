"""memory.py — 记忆管理路由。

包含：
  POST /api/memory/mode   — 切换记忆模式 (task 87 Phase 6)
  POST /api/memory/add    — 添加记忆条目
  POST /api/memory/remove — 删除记忆条目
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from schemas._common import COMMON_ERROR_RESPONSES, StateResponse
from schemas.memory import MemoryAddRequest, MemoryModeRequest, MemoryRemoveRequest

router = APIRouter()


@router.post("/api/memory/mode", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_memory_mode(body: MemoryModeRequest, request: Request) -> JSONResponse:
    """task 87 Phase 6: UI 按钮也走 dispatcher,获得统一审计 + destructive 检查。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _require_api_user,
        _resolve_persist_target,
    )
    api_user = _require_api_user(request)
    body_dict = body.model_dump(exclude_none=True)
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="set_memory_mode",
        args={"mode": body_dict.get("mode", "normal")},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@router.post("/api/memory/add", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_memory_add(body: MemoryAddRequest, request: Request) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher 的 add_memory_* 工具系列。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _require_api_user,
        _resolve_persist_target,
    )
    api_user = _require_api_user(request)
    body_dict = body.model_dump(exclude_none=True)
    state = _ensure_loaded(api_user)
    bucket = body_dict.get("bucket", "notes")
    text = body_dict.get("text", "")
    # bucket → 对应工具名
    bucket_tool = {
        "facts": "add_memory_fact",
        "resources": "add_memory_resource",
        "abilities": "add_memory_ability",
        "pinned": "pin_memory",
        "notes": "add_memory_note",
    }.get(bucket, "add_memory_note")
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name=bucket_tool,
        args={"text": text},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@router.post("/api/memory/remove", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_memory_remove(body: MemoryRemoveRequest, request: Request) -> JSONResponse:
    """task 87 Phase 6: destructive 走 dispatcher remove_memory_item 工具。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _require_api_user,
        _resolve_persist_target,
    )
    api_user = _require_api_user(request)
    body_dict = body.model_dump(exclude_none=True)
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="remove_memory_item",
        args={
            "bucket": body_dict.get("bucket", "notes"),
            "index": int(body_dict.get("index", -1)),
        },
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})
