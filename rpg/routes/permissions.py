"""permissions.py — 权限/确认管理路由。

包含：
  POST /api/permissions              — 切换权限模式 (task 87 Phase 6)
  POST /api/permissions/pending-write — 审批待写入 (task #53)
  POST /api/questions/clear          — 回答/跳过 GM 询问
  POST /api/debug/pending-question   — [debug] 注入待处理问题
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_admin, get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, ErrorResponse, StateResponse
from schemas.permissions import (
    DebugPendingQuestionRequest,
    PendingWriteRequest,
    PermissionsRequest,
    QuestionClearRequest,
)

router = APIRouter()


@router.post("/api/permissions", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_permissions(
    body: PermissionsRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 87 Phase 6: 敏感权限切换走 dispatcher (origin=ui_button)。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    body_dict = body.model_dump(exclude_none=True)
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="set_permission_mode",
        args={"mode": body_dict.get("mode", "full_access")},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@router.post("/api/permissions/pending-write", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_pending_write(
    body: PendingWriteRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """审批一条待写入。前端发 {id, action} 或 {index, decision}（兼容老 contract）。

    P0 修复（task #53）：之前后端只读 index+decision，前端发 id+action →
    /set 后端 body.get("index", -1) = -1 → "待审写入不存在" → 整个审批流死。
    现在按 id 优先（稳定），index/decision 作 fallback。
    """
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    body_dict = body.model_dump(exclude_none=True)
    state = _ensure_loaded(api_user)
    item_id = body_dict.get("id")
    raw_index = body_dict.get("index")
    index = int(raw_index) if raw_index is not None else None
    decision = str(body_dict.get("action") or body_dict.get("decision") or "").lower()
    # task 87 Phase 6: 走 dispatcher 的 approve/reject_pending_write 工具。
    # 老路径 (state.approve_pending_write/reject_pending_write) 接受 index 旧契约,
    # 工具只用 id; index 仅 fallback。
    if decision == "approve":
        tool_name = "approve_pending_write"
    elif decision == "reject":
        tool_name = "reject_pending_write"
    else:
        return JSONResponse({"ok": False, "error": "缺少 action/decision（approve|reject）"}, status_code=400)
    if not item_id and index is not None:
        # 旧契约 index → 在 pending_writes 里找 id 兜底
        pws = (state.data.get("permissions") or {}).get("pending_writes") or []
        if 0 <= index < len(pws):
            item_id = pws[index].get("id")
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    d_result = dispatch_ui_tool(
        tool_name=tool_name,
        args={"id": item_id or ""},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not d_result.ok:
        # dispatcher 失败 → fallback 到老路径(向后兼容,例如老存档无 id 时)
        if decision == "approve":
            result = state.approve_pending_write(index=index, id=item_id)
        else:
            result = state.reject_pending_write(index=index, id=item_id)
    else:
        result = d_result.result
    state.data["memory"]["last_structured_updates"] = [result] + state.data["memory"].get("last_structured_updates", [])[:11]
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "result": result, "state": _payload(api_user)})


@router.post("/api/questions/clear", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_question_clear(
    body: QuestionClearRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """回答(或跳过)一条 GM 询问。{id, choice?} 或 {index, choice?}。
    task 87 Phase 6: 走 dispatcher dismiss_pending_question。choice 走老路径
    (clear_pending_question 支持记录玩家选择,工具暂不支持 choice)。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    body_dict = body.model_dump(exclude_none=True)
    state = _ensure_loaded(api_user)
    item_id = body_dict.get("id")
    raw_index = body_dict.get("index")
    index = int(raw_index) if raw_index is not None else None
    choice = body_dict.get("choice")
    # 若有 choice (玩家选了选项),走老路径以保留 choice 记录;若仅 dismiss → dispatcher
    if choice or not item_id:
        popped = state.clear_pending_question(index=index, id=item_id, choice=choice)
    else:
        from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
        d_result = dispatch_ui_tool(
            tool_name="dismiss_pending_question",
            args={"id": item_id},
            user_id=int(api_user.get("id")) if api_user else 0,
            save_id=_resolve_persist_target(api_user)[1] or 0,
            state=state,
        )
        popped = d_result.ok  # type: ignore[assignment]
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "cleared": bool(popped), "state": _payload(api_user)})


@router.post("/api/debug/pending-question", response_model=StateResponse, responses={**COMMON_ERROR_RESPONSES, 404: {"model": ErrorResponse}})
async def api_debug_pending_question(
    body: DebugPendingQuestionRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    """task 87 Phase 6: debug 注入也走 dispatcher 的 inject_pending_question 工具。"""
    from app import _ensure_loaded, _payload, _resolve_persist_target
    from core.config import debug_ui as _debug_ui
    if not _debug_ui():
        return JSONResponse({"ok": False, "error": "debug disabled"}, status_code=404)
    body_dict = body.model_dump(exclude_none=True)
    state = _ensure_loaded(api_user)
    # 把老 text+| 分隔 options 拆成 question + options 列表
    raw_text = body_dict.get("text") or "下一步怎么做？｜选项：继续调查、返回基地、询问同伴"
    if "｜选项：" in raw_text:
        question, _, opt_str = raw_text.partition("｜选项：")
        options = [s.strip() for s in opt_str.split("、") if s.strip()]
    else:
        question, options = raw_text, []
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    d_result = dispatch_ui_tool(
        tool_name="inject_pending_question",
        args={"question": question, "options": options, "source": "debug"},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not d_result.ok:
        return JSONResponse({"ok": False, "error": d_result.error}, status_code=400)
    state.save()
    return JSONResponse({"ok": True, "state": _payload(api_user)})
