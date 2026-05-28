"""console_assistant.conversations — 对话生命周期管理。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from console_assistant import _state


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_conversation_id() -> str:
    import uuid
    return f"conv-{uuid.uuid4().hex[:12]}"


def _new_trace_id() -> str:
    import secrets
    return f"console-{secrets.token_urlsafe(6)}"


def _new_call_id() -> str:
    import secrets
    return f"cc-{secrets.token_urlsafe(6)}"


def _gc_user_bucket(user_bucket: dict[str, dict[str, Any]]) -> None:
    """简单 TTL + LRU 维持 bucket 大小。"""
    if not user_bucket:
        return
    cutoff = datetime.now().timestamp() - _state.CONVERSATION_TTL_SECONDS
    drop = []
    for cid, conv in user_bucket.items():
        try:
            ts = datetime.fromisoformat(conv["last_used"]).timestamp()
        except Exception:
            ts = 0
        if ts < cutoff:
            drop.append(cid)
    for cid in drop:
        user_bucket.pop(cid, None)
    if len(user_bucket) > _state.MAX_CONVERSATIONS_PER_USER:
        items = sorted(
            user_bucket.items(),
            key=lambda kv: kv[1].get("last_used", ""),
        )
        for cid, _ in items[: len(user_bucket) - _state.MAX_CONVERSATIONS_PER_USER]:
            user_bucket.pop(cid, None)


def _trim_messages(conv: dict[str, Any]) -> None:
    msgs = conv.get("messages") or []
    if len(msgs) > _state.MAX_MESSAGES_PER_CONVERSATION:
        conv["messages"] = msgs[-_state.MAX_MESSAGES_PER_CONVERSATION:]


def _get_or_create_conversation(
    user_id: int, conversation_id: str | None,
) -> tuple[str, dict[str, Any]]:
    """按 user_id+conversation_id 取或新建。返回 (conversation_id, conv_state)。"""
    with _state._lock:
        user_bucket = _state._conversations.setdefault(user_id, {})
        _gc_user_bucket(user_bucket)
        if conversation_id and conversation_id in user_bucket:
            conv = user_bucket[conversation_id]
            conv["last_used"] = _now_iso()
            return conversation_id, conv
        new_id = conversation_id or _new_conversation_id()
        conv = {
            "messages": [],
            "pending_confirmations": {},
            "created_at": _now_iso(),
            "last_used": _now_iso(),
            "cum_input_tokens": 0,
            "cum_output_tokens": 0,
            "context_limit": 0,
            "last_user_message": "",
        }
        user_bucket[new_id] = conv
        return new_id, conv


def new_conversation(user_id: int) -> str:
    """task 111: 显式开新对话 (用户点 '新建对话' 按钮)。"""
    with _state._lock:
        user_bucket = _state._conversations.setdefault(user_id, {})
        _gc_user_bucket(user_bucket)
        new_id = _new_conversation_id()
        user_bucket[new_id] = {
            "messages": [],
            "pending_confirmations": {},
            "created_at": _now_iso(),
            "last_used": _now_iso(),
            "cum_input_tokens": 0,
            "cum_output_tokens": 0,
            "context_limit": 0,
            "last_user_message": "",
        }
        return new_id


def list_conversations(user_id: int) -> list[dict[str, Any]]:
    """task 111: 列当前用户所有对话,按 last_used 倒序。"""
    with _state._lock:
        bucket = _state._conversations.get(user_id, {})
        _gc_user_bucket(bucket)
        out = []
        for cid, conv in bucket.items():
            out.append({
                "id": cid,
                "created_at": conv.get("created_at", ""),
                "last_used": conv.get("last_used", ""),
                "message_count": len(conv.get("messages") or []),
                "cum_input_tokens": int(conv.get("cum_input_tokens", 0)),
                "cum_output_tokens": int(conv.get("cum_output_tokens", 0)),
                "context_limit": int(conv.get("context_limit", 0)),
                "last_user_message": (conv.get("last_user_message", "") or "")[:50],
            })
        out.sort(key=lambda r: r.get("last_used", ""), reverse=True)
        return out


def delete_conversation(user_id: int, conversation_id: str) -> bool:
    """task 111: 删某个对话。"""
    with _state._lock:
        bucket = _state._conversations.get(user_id, {})
        return bucket.pop(conversation_id, None) is not None


def get_conversation_state(user_id: int) -> dict[str, dict[str, Any]]:
    """test hook: 直接拿某用户的全部 conversation。"""
    return _state._conversations.get(user_id, {})


def reset_all_conversations() -> None:
    """test hook: 进程内清空。"""
    with _state._lock:
        _state._conversations.clear()
