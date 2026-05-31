"""
user_credentials.py — 用户级 API key CRUD + 解密读取

调用入口：
- set_credential(user_id, api_id, plaintext_key, base_url_override="")
- get_credential(user_id, api_id) → 明文 key 或空串
- list_credentials(user_id) → 不返回 key 本身，只返回存在与否、最近更新时间
- delete_credential(user_id, api_id)
- resolve_api_key(user_id, api_id, env_fallback) → 解密 → 环境变量回退（仅 admin/本地）

设计原则：
- DB 里永远是密文
- 解密只在调用 LLM 时即时做，结果不缓存
- list 接口永远不返回 raw key，只给 has_credential 布尔标记
"""
from __future__ import annotations

import os
from typing import Any

from psycopg.types.json import Jsonb

from utils.crypto import decrypt_api_key, encrypt_api_key

from .db import connect, expose, init_db

_PRIVATE_HOST_PREFIXES = (
    "127.", "10.", "192.168.", "169.254.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "0.", "localhost", "::1", "fc", "fd", "fe80",
)


def _validate_base_url(url: str) -> None:
    """禁止把 base_url 指向私网/本机，避免 SSRF。"""
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
    except Exception as exc:
        raise ValueError("base_url 必须是合法 URL") from exc
    if p.scheme not in {"https", "http"}:
        raise ValueError("base_url 必须是 http/https")
    # 生产建议只允许 https；本地 admin 调试可允许 http
    from core.config import require_auth as _require_auth
    if p.scheme == "http" and _require_auth():
        raise ValueError("服务器模式下 base_url 必须是 https")
    host = (p.hostname or "").lower()
    if not host:
        raise ValueError("base_url 缺少 host")
    for prefix in _PRIVATE_HOST_PREFIXES:
        if host == prefix.rstrip(".") or host.startswith(prefix):
            raise ValueError(f"base_url 不允许指向私有/本地地址：{host}")


def set_credential(user_id: int, api_id: str, plaintext_key: str, base_url_override: str = "", enabled: bool = True, *, allow_base_url: bool = False) -> dict[str, Any]:
    """加密保存。空 key 等价于删除该 credential。

    安全：base_url_override 是 SSRF 风险源。allow_base_url 默认 False，
    意味着普通用户无法用自己的 key 让服务器访问任意 URL（如 127.0.0.1）。
    本地匿名模式 / admin 设置时调用方传 allow_base_url=True 才能写入。
    """
    init_db()
    api_id = (api_id or "").strip()
    if not api_id:
        raise ValueError("api_id 不能为空")
    if not plaintext_key:
        return delete_credential(user_id, api_id)
    # P1 #7：之前非 admin 传 base_url_override 直接静默 = ""，UI 以为已设置。
    # 改成显式 raise ValueError，让 /api/me/credentials 回 400，前端能感知。
    if base_url_override and not allow_base_url:
        raise ValueError("base_url_override 仅管理员可设置 · 普通用户必须使用 catalog 中的 base_url")
    if not allow_base_url:
        base_url_override = ""
    elif base_url_override:
        _validate_base_url(base_url_override)
    encrypted = encrypt_api_key(plaintext_key, user_id, api_id)
    with connect() as db:
        row = db.execute(
            """
            insert into user_api_credentials(user_id, api_id, encrypted_key, base_url_override, enabled, metadata)
            values (%s, %s, %s, %s, %s, %s)
            on conflict(user_id, api_id) do update set
              encrypted_key = excluded.encrypted_key,
              base_url_override = excluded.base_url_override,
              enabled = excluded.enabled,
              metadata = excluded.metadata,
              updated_at = now()
            returning id, user_id, api_id, base_url_override, enabled, updated_at
            """,
            (user_id, api_id, encrypted, base_url_override or "", enabled, Jsonb({})),
        ).fetchone()
    return {"ok": True, **(expose(row) or {}), "has_credential": True}


def delete_credential(user_id: int, api_id: str) -> dict[str, Any]:
    init_db()
    with connect() as db:
        db.execute(
            "delete from user_api_credentials where user_id = %s and api_id = %s",
            (user_id, api_id),
        )
    return {"ok": True, "deleted": True, "api_id": api_id}


def list_credentials(user_id: int) -> dict[str, Any]:
    """返回用户已配置的 API 凭证列表（不含 raw key）"""
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select user_id, api_id, base_url_override, enabled, created_at, updated_at,
                   length(encrypted_key) as cipher_len
            from user_api_credentials
            where user_id = %s
            order by api_id
            """,
            (user_id,),
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "api_id": r["api_id"],
            "has_credential": int(r["cipher_len"] or 0) > 0,
            "base_url_override": r["base_url_override"] or "",
            "enabled": bool(r["enabled"]),
            "updated_at": str(r["updated_at"]),
        })
    return {"ok": True, "items": items, "total": len(items)}


def get_credential(user_id: int, api_id: str) -> dict[str, Any] | None:
    """返回包含明文 key 的 dict（调用方负责不写日志/不返回前端）。失败返回 None。"""
    init_db()
    with connect() as db:
        row = db.execute(
            "select * from user_api_credentials where user_id = %s and api_id = %s",
            (user_id, api_id),
        ).fetchone()
    if not row or not row.get("enabled"):
        return None
    plaintext = decrypt_api_key(row.get("encrypted_key"), user_id, api_id)
    if not plaintext:
        return None
    return {
        "api_id": api_id,
        "key": plaintext,
        "base_url_override": row.get("base_url_override") or "",
    }


def resolve_api_key(user_id: int | None, api_id: str, env_fallback: str = "") -> dict[str, Any]:
    """
    GM 调用入口：按用户隔离取 key。

    解析顺序：
    1. 当前 user 在 user_api_credentials 表里的 key（绝对隔离）
    2. 本地未登录 + 环境变量（仅 RPG_REQUIRE_AUTH != 1 时允许）

    返回 {"key": "...", "source": "user_db" | "env" | "none", "base_url_override": "..."}

    内部使用 request-scoped cache（core.request_cache.get_api_cred_cached），
    同一请求内相同 (user_id, api_id) 只查一次 DB；非请求上下文行为不变。
    """
    if user_id:
        try:
            from core.request_cache import get_api_cred_cached
            cred = get_api_cred_cached(int(user_id), api_id)
        except Exception:
            cred = get_credential(user_id, api_id)
        if cred and cred.get("key"):
            return {"key": cred["key"], "source": "user_db", "base_url_override": cred.get("base_url_override", "")}

    # 仅未强制鉴权时允许环境变量回退
    from core.config import require_auth as _require_auth
    if _require_auth():
        return {"key": "", "source": "none", "base_url_override": ""}
    if env_fallback:
        env_key = os.environ.get(env_fallback)
        if env_key:
            return {"key": env_key, "source": "env", "base_url_override": ""}
    return {"key": "", "source": "none", "base_url_override": ""}
