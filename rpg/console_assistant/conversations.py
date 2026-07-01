"""console_assistant.conversations — 对话生命周期管理。"""
from __future__ import annotations

import json as _json
import time
from datetime import datetime
from typing import Any

from console_assistant import _state


# ────────────────────────────────────────────────────────────
# 跨 worker 共享(Redis):进程内 _conversations dict 在 workers>1 时各进程独立,
# /chat 建对话(worker A) → /confirm 落到 worker B 找不到 → "conversation 不存在"。
# 把对话(含 messages + pending_confirmations)落 Redis,任意 worker 可读。
# Redis 不可用 → 静默降级回进程内 dict(单 worker 下行为不变)。
# ────────────────────────────────────────────────────────────
def _conv_redis_key(user_id: int, cid: str) -> str:
    return f"console_conv:{int(user_id)}:{cid}"


def persist_conversation(user_id: int, cid: str, conv: dict[str, Any]) -> None:
    """每个 /chat、/confirm 回合结束后调用,把对话写回 Redis(TTL=对话 TTL)。"""
    if not cid or not isinstance(conv, dict):
        return
    # Redis 是 6h 热缓存;写它是【尽力而为】—— 未配置/不可达都不能影响 PG 永久落库。
    # 原实现把 is_enabled()/无 cli 的 return 放在 _persist_conv_pg 之前 → Redis 关掉时(本地/桌面
    # 无 Redis)PG 永不写、对话重启即丢,与注释「再落 PG 永久保留」正好相反。改为解耦。
    try:
        import redis_bus
        if redis_bus.is_enabled():
            cli = redis_bus.get_sync_client()
            if cli:
                cli.setex(_conv_redis_key(user_id, cid), _state.CONVERSATION_TTL_SECONDS,
                          _json.dumps(conv, ensure_ascii=False, default=str))
    except Exception:
        pass
    # PG 无条件写(永久保留:刷新/超时/重启/换 worker 都能还原),与 Redis 是否可用解耦。
    _persist_conv_pg(user_id, cid, conv)


def _load_conv_redis(user_id: int, cid: str) -> dict[str, Any] | None:
    if not cid:
        return None
    try:
        import redis_bus
        if not redis_bus.is_enabled():
            return None
        cli = redis_bus.get_sync_client()
        if not cli:
            return None
        raw = cli.get(_conv_redis_key(user_id, cid))
        if not raw:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        conv = _json.loads(raw)
        return conv if isinstance(conv, dict) else None
    except Exception:
        return None


# ── PG 永久持久化(Redis 6h 之外):对话落 console_conversations,刷新/超时/重启都还原 ──
def _persist_conv_pg(user_id: int, cid: str, conv: dict[str, Any]) -> None:
    if not (user_id and cid and isinstance(conv, dict)):
        return
    try:
        from psycopg.types.json import Jsonb

        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            db.execute(
                "insert into console_conversations(user_id, conversation_id, conv, updated_at)"
                " values (%s,%s,%s, now())"
                " on conflict (user_id, conversation_id) do update set conv=excluded.conv, updated_at=now()",
                (int(user_id), cid, Jsonb(conv)),
            )
            if hasattr(db, "commit"):
                db.commit()
    except Exception:
        pass


def list_conversations_pg(user_id: int) -> list[dict[str, Any]]:
    """从 PG 列对话(进程内 dict 在 worker 重启后为空,此为持久兜底)。返回与 list_conversations 同形。"""
    if not user_id:
        return []
    out: list[dict[str, Any]] = []
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select conversation_id, conv, updated_at from console_conversations "
                "where user_id=%s order by updated_at desc limit 100",
                (int(user_id),),
            ).fetchall()
        for r in rows:
            conv = (r.get("conv") if isinstance(r, dict) else r["conv"]) or {}
            if not isinstance(conv, dict):
                conv = {}
            msgs = conv.get("messages") or []
            out.append({
                "id": r["conversation_id"],
                "created_at": conv.get("created_at", ""),
                "last_used": conv.get("last_used", "") or str(r["updated_at"]),
                "message_count": len(msgs),
                "cum_input_tokens": int(conv.get("cum_input_tokens", 0) or 0),
                "cum_output_tokens": int(conv.get("cum_output_tokens", 0) or 0),
                "context_limit": int(conv.get("context_limit", 0) or 0),
                "last_user_message": (conv.get("last_user_message", "") or "")[:50],
            })
    except Exception:
        pass
    return out


def _load_conv_pg(user_id: int, cid: str) -> dict[str, Any] | None:
    if not (user_id and cid):
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            r = db.execute(
                "select conv from console_conversations where user_id=%s and conversation_id=%s",
                (int(user_id), cid),
            ).fetchone()
        conv = (r or {}).get("conv") if r else None
        return conv if isinstance(conv, dict) else None
    except Exception:
        return None

# GC 节流: 每 60 秒最多触发一次进程级 GC, 避免每次读都扫全桶
_last_gc_at: float = 0.0
_GC_INTERVAL = 60.0


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


def _maybe_gc(user_bucket: dict[str, dict[str, Any]]) -> None:
    """写路径 GC 节流入口: 60 秒内最多触发一次。"""
    global _last_gc_at
    now = time.monotonic()
    if now - _last_gc_at >= _GC_INTERVAL:
        _gc_user_bucket(user_bucket)
        _last_gc_at = now


def _get_or_create_conversation(
    user_id: int, conversation_id: str | None,
) -> tuple[str, dict[str, Any]]:
    """按 user_id+conversation_id 取或新建。返回 (conversation_id, conv_state)。"""
    with _state._lock:
        user_bucket = _state._conversations.setdefault(user_id, {})
        _maybe_gc(user_bucket)
        if conversation_id and conversation_id in user_bucket:
            conv = user_bucket[conversation_id]
            conv["last_used"] = _now_iso()
            return conversation_id, conv
        # 本进程无此对话 → 试从 Redis 拉(多 worker:别的 worker 建的对话续聊),Redis 未命中
        # (超 6h / 重置)再从 PG 拉(永久持久化)。
        if conversation_id:
            loaded = _load_conv_redis(user_id, conversation_id) or _load_conv_pg(user_id, conversation_id)
            if loaded is not None:
                loaded.setdefault("pending_confirmations", {})
                loaded["last_used"] = _now_iso()
                user_bucket[conversation_id] = loaded
                return conversation_id, loaded
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
        _maybe_gc(user_bucket)
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
        persist_conversation(user_id, new_id, user_bucket[new_id])
        return new_id


def list_conversations(user_id: int) -> list[dict[str, Any]]:
    """task 111: 列当前用户所有对话,按 last_used 倒序。"""
    with _state._lock:
        bucket = _state._conversations.get(user_id, {})
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
    """task 111: 删某个对话(连带 Redis 副本)。"""
    with _state._lock:
        bucket = _state._conversations.get(user_id, {})
        local = bucket.pop(conversation_id, None) is not None
    redis_hit = False
    try:
        import redis_bus
        if redis_bus.is_enabled():
            cli = redis_bus.get_sync_client()
            if cli:
                redis_hit = bool(cli.delete(_conv_redis_key(user_id, conversation_id)))
    except Exception:
        pass
    pg_hit = False
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cur = db.execute("delete from console_conversations where user_id=%s and conversation_id=%s",
                             (int(user_id), conversation_id))
            pg_hit = bool(getattr(cur, "rowcount", 0))
            if hasattr(db, "commit"):
                db.commit()
    except Exception:
        pass
    return local or redis_hit or pg_hit


def _test_only_get_conversation_state(user_id: int) -> dict[str, dict[str, Any]]:
    """Test-only — DO NOT call from routes."""
    return _state._conversations.get(user_id, {})


# backward-compat alias (tests that import old name will still work until updated)
get_conversation_state = _test_only_get_conversation_state


def _test_only_reset_all_conversations() -> None:
    """Test-only — DO NOT call from routes."""
    with _state._lock:
        _state._conversations.clear()


# backward-compat alias
reset_all_conversations = _test_only_reset_all_conversations
