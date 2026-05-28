"""console_assistant.py — 侧栏控制台助手路由 (/api/console_assistant/*)。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from routes._deps_fastapi import get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, GenericOkResponse, OkResponse
from schemas.console_assistant import (
    ConsoleAssistantChatRequest,
    ConsoleAssistantConfirmRequest,
    ConsoleAssistantDeleteConversationRequest,
)

router = APIRouter()


@router.get("/api/console_assistant/ping")
async def api_console_assistant_ping() -> JSONResponse:
    """task 48: 给前端探测后端是否就绪,200 = 真后端可用 (前端切走 mock)。"""
    return JSONResponse({"ok": True, "service": "console_assistant", "version": "1"})


@router.get("/api/console_assistant/conversations")
async def api_console_assistant_conversations(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 111: 列当前用户所有对话。"""
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"items": []})
    from console_assistant import list_conversations
    items = list_conversations(user_id)
    return JSONResponse({"items": items})


@router.post("/api/console_assistant/new_conversation", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_console_assistant_new_conversation(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 111: 开新对话, 返新 conversation_id。"""
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"ok": False, "error": "需要登录"}, status_code=401)
    from console_assistant import new_conversation
    new_id = new_conversation(user_id)
    return JSONResponse({"ok": True, "conversation_id": new_id})


@router.post("/api/console_assistant/delete_conversation", response_model=OkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_console_assistant_delete_conversation(
    body: ConsoleAssistantDeleteConversationRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task 111: 删除某对话。"""
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return JSONResponse({"ok": False, "error": "需要登录"}, status_code=401)
    body_dict = body.model_dump(exclude_none=True)
    cid = str(body_dict.get("conversation_id") or "").strip()
    if not cid:
        return JSONResponse({"ok": False, "error": "conversation_id 必填"}, status_code=400)
    from console_assistant import delete_conversation
    ok = delete_conversation(user_id, cid)
    return JSONResponse({"ok": ok})


@router.post("/api/console_assistant/chat")
async def api_console_assistant_chat(
    body: ConsoleAssistantChatRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    """task 48: 侧栏助手主聊天 SSE endpoint。

    body: { message: str, conversation_id?: str, page_context?: dict }
    SSE: meta / token / tool_call / tool_result / confirmation_required / error / done
    """
    from app import _ensure_loaded, _resolve_console_assistant_backend
    body_dict = body.model_dump(exclude_none=True)
    message = str(body_dict.get("message") or "").strip()
    conversation_id = body_dict.get("conversation_id")
    if isinstance(conversation_id, str):
        conversation_id = conversation_id.strip() or None
    else:
        conversation_id = None
    page_context = body_dict.get("page_context") if isinstance(body_dict.get("page_context"), dict) else None

    if not message:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'空消息'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )

    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'需要登录'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )

    # 注意:这里 state_provider 用 _ensure_loaded — 只有 save scope 工具用得到。
    def _sp(env):
        try:
            if env.save_id is None:
                return None
            return _ensure_loaded(api_user)
        except Exception:
            return None

    # 解析 backend
    try:
        backend = _resolve_console_assistant_backend(api_user)
    except Exception as exc:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':f'backend 初始化失败: {exc}'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )

    from console_assistant import stream_chat as _stream_chat

    def _gen():
        yield from _stream_chat(
            user_id=user_id,
            message=message,
            conversation_id=conversation_id,
            page_context=page_context,
            backend=backend,
            state_provider=_sp,
        )

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/api/console_assistant/confirm")
async def api_console_assistant_confirm(
    body: ConsoleAssistantConfirmRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    """task 58: 对一个 pending destructive 工具调用做决策, 返 SSE 流。

    body: { conversation_id: str, call_id: str, decision: 'approve'|'reject',
            page_context?: dict }
    SSE: 与 /chat endpoint 同款 (meta / tool_call / tool_result / token /
         confirmation_required / navigation_required / error / done)

    旧 JSON 协议已弃用 — 修复:用户点确认后 LLM 必须基于工具结果续写,
    否则对话直接断在工具结果。
    """
    from app import _ensure_loaded, _resolve_console_assistant_backend
    body_dict = body.model_dump(exclude_none=True)
    conversation_id = str(body_dict.get("conversation_id") or "").strip()
    call_id = str(body_dict.get("call_id") or "").strip()
    decision = str(body_dict.get("decision") or "").strip().lower()
    page_context = body_dict.get("page_context") if isinstance(body_dict.get("page_context"), dict) else None
    if not conversation_id or not call_id or decision not in {"approve", "reject"}:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'conversation_id / call_id / decision 必填; decision ∈ {approve,reject}'}, ensure_ascii=False)}\n\n",
                  "event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
            status_code=400,
        )

    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'需要登录'}, ensure_ascii=False)}\n\n",
                  "event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
            status_code=401,
        )

    def _sp(env):
        try:
            if env.save_id is None:
                return None
            return _ensure_loaded(api_user)
        except Exception:
            return None

    try:
        backend = _resolve_console_assistant_backend(api_user)
    except Exception as exc:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':f'backend 初始化失败: {exc}'}, ensure_ascii=False)}\n\n",
                  "event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
        )

    from console_assistant import apply_confirmation_stream as _apply_stream

    def _gen():
        yield from _apply_stream(
            user_id=user_id,
            conversation_id=conversation_id,
            call_id=call_id,
            decision=decision,
            page_context=page_context,
            backend=backend,
            state_provider=_sp,
        )

    return StreamingResponse(_gen(), media_type="text/event-stream")
