"""core.request_cache — Request-scoped in-memory cache for hot DB lookups.

目标: 一个 HTTP 请求内 user_preferences / user_api_credentials 只查一次 DB。

用法 (middleware 层):
    from core.request_cache import reset_request_caches
    reset_request_caches()   # 每个请求开始时调用

业务层透明: llm_backend / user_credentials 内部用此模块替代裸 SELECT,
调用方签名完全不变。

非请求上下文 (cron / 直接调用 / tests):
    ContextVar 默认值为 None,get_* 函数检测到后每次直接查 DB 不缓存,
    行为与改造前完全一致。

contextvars 在 asyncio.to_thread 中的传播:
    Python 3.7+ 的 asyncio.to_thread / ThreadPoolExecutor 会将父协程的
    contextvars 复制到子线程(copy-on-write),写操作不会反向传播。
    这里 cache dict 是可变对象,子线程向同一 dict 写入是安全的——
    所有线程共享同一请求的 cache 实例,不会出现漏缓存的问题。
"""
from __future__ import annotations

import contextvars
from typing import Any

# None = 非请求上下文; dict = 请求内缓存容器
_user_prefs_cache: contextvars.ContextVar[dict[int, dict] | None] = (
    contextvars.ContextVar("_user_prefs_cache", default=None)
)
_api_creds_cache: contextvars.ContextVar[dict[tuple, Any] | None] = (
    contextvars.ContextVar("_api_creds_cache", default=None)
)


def reset_request_caches() -> None:
    """每个 HTTP 请求开始时由 middleware 调用,清空/初始化两个缓存容器。"""
    _user_prefs_cache.set({})
    _api_creds_cache.set({})


# ── user_preferences ────────────────────────────────────────────────────────

def get_user_prefs_cached(user_id: int) -> dict:
    """返回该 user_id 的 preferences JSONB dict。

    请求内第一次访问 → SELECT;后续直接命中 cache。
    非请求上下文 → 每次查 DB。
    """
    cache = _user_prefs_cache.get()
    if cache is None:
        # 非请求上下文,不缓存
        return _select_all_prefs(user_id)
    if user_id not in cache:
        cache[user_id] = _select_all_prefs(user_id)
    return cache[user_id]


def invalidate_user_prefs_cache(user_id: int) -> None:
    """写完 user_preferences 后清掉本请求内该 user 的 prefs 缓存。

    否则同一请求里在写 prefs 之后再调 get_user_prefs_cached / resolve_preferred_*
    会命中"写前"的快照,读到旧的 selected 模型(模型选择器"刷新后回退"症状之一)。
    非请求上下文(cache=None)为无操作。
    """
    cache = _user_prefs_cache.get()
    if cache is not None:
        cache.pop(int(user_id), None)


def _select_all_prefs(user_id: int) -> dict:
    """一次 SELECT,返回 preferences dict(失败则返回 {})。"""
    try:
        from platform_app.db import connect, init_db  # type: ignore[import]

        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(user_id),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return row["preferences"]
    except Exception:
        pass
    return {}


# ── user_api_credentials ────────────────────────────────────────────────────

def get_api_cred_cached(user_id: int, api_id: str) -> dict | None:
    """返回 get_credential(user_id, api_id) 的结果(含明文 key)。

    请求内相同 (user_id, api_id) 只查一次;非请求上下文每次查。

    None 意为凭据不存在/未启用,也被缓存(避免对同一无效 key 重复查 DB)。
    """
    cache = _api_creds_cache.get()
    key = (user_id, api_id)
    if cache is None:
        return _fetch_cred(user_id, api_id)
    if key not in cache:
        cache[key] = _fetch_cred(user_id, api_id)
    return cache[key]


def _fetch_cred(user_id: int, api_id: str) -> dict | None:
    """直接调 get_credential,绕过缓存层。"""
    try:
        from platform_app.user_credentials import get_credential  # type: ignore[import]

        return get_credential(user_id, api_id)
    except Exception:
        return None


__all__ = [
    "reset_request_caches",
    "get_user_prefs_cached",
    "invalidate_user_prefs_cache",
    "get_api_cred_cached",
]
