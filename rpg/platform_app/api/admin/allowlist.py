"""platform_app.api.admin.allowlist —— 2.x 注册白名单批量导入 + 内部跨服务推送端点。纯机械搬家,行为零变化。

/api/admin/allowlist(bulk upsert / 列表)+ /api/internal/allowlist/bulk(ECS02↔ECS06
跨服务共享 secret 认证,免 require_admin)。
"""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, Request

from ...db import connect, limit_value
from .._deps import _client_ip, json_response
from ._shared import _require_admin, router


@router.post("/api/admin/allowlist/bulk")
async def api_bulk_allowlist(request: Request, admin=Depends(_require_admin)):
    """批量 upsert registration_allowlist。
    body: {entries: [{email, magic_token, batch?}, ...]}
    用于 landing 邮件批次：生成 magic_token 后由运营写入白名单。
    """
    body = await request.json()
    entries = body.get("entries") or []
    inserted = 0
    with connect() as db:
        for e in entries:
            email = (e.get("email") or "").strip().lower()
            token = (e.get("magic_token") or "").strip()
            if not email or not token:
                continue
            db.execute(
                """
                insert into registration_allowlist (email_norm, magic_token, batch, source)
                values (%s, %s, %s, 'landing-batch')
                on conflict (email_norm) do update set
                  magic_token = excluded.magic_token,
                  batch = excluded.batch
                """,
                (email, token, int(e.get("batch") or 1)),
            )
            inserted += 1
    return json_response({"ok": True, "inserted": inserted})


@router.get("/api/admin/allowlist")
async def api_list_allowlist(
    limit: int = 100,
    batch: int = 0,
    admin=Depends(_require_admin),
):
    """列出白名单条目（管理员用）。"""
    limit = limit_value(limit, default=100, maximum=500)
    with connect() as db:
        if batch:
            rows = db.execute(
                "select email_norm, magic_token, batch, source, created_at, used_by_user_id, used_at "
                "from registration_allowlist where batch = %s order by created_at desc limit %s",
                (batch, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "select email_norm, magic_token, batch, source, created_at, used_by_user_id, used_at "
                "from registration_allowlist order by created_at desc limit %s",
                (limit,),
            ).fetchall()
    # SEC(M-8): magic_token 脱敏 —— admin 列表无需回显完整邀请 token(token 已随邮件下发)。
    # 入侵任一 admin session 即可批量导出全量未用邀请并持久冒充,故仅显示前缀 + ***。
    def _mask_token(t):
        t = (t or "")
        return (t[:8] + "***") if t else ""
    entries = []
    for r in rows:
        d = dict(r)
        d["magic_token"] = _mask_token(d.get("magic_token"))
        entries.append(d)
    return json_response({
        "ok": True,
        "entries": entries,
        "count": len(entries),
    })


@router.post("/api/internal/allowlist/bulk")
async def api_internal_allowlist_bulk(request: Request):
    """task: 跨服务推送 — landing-deploy/backend/send_invites.py 用此 endpoint
    把 batch=N 的 (email, magic_token) 批量写入 RPG registration_allowlist。

    认证:Header X-Internal-Secret 必须匹配 env RPG_ALLOWLIST_SHARED_SECRET。
    无 session/cookie 要求,免 require_admin(因为是 ECS02 ↔ ECS06 跨服务调用)。

    Body: {"entries": [{"email": "x@x", "magic_token": "AbC...", "batch": 1}, ...]}
    Return: {"ok": true, "inserted": N, "updated": M}
    """
    expected = os.environ.get("RPG_ALLOWLIST_SHARED_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="endpoint disabled (RPG_ALLOWLIST_SHARED_SECRET not set)")
    # SEC(M-7): per-IP 速率上限,blunt 对共享 secret 的暴力探测(纵深防御;首选仍是 nginx 内网白名单)。
    try:
        import redis_bus as _rb
        _ip = _client_ip(request) or "-"
        _c = _rb.rate_incr(f"intl_allowlist:{_ip}", 60)
        if _c and _c > 20:
            raise HTTPException(status_code=429, detail="rate limited")
    except HTTPException:
        raise
    except Exception:
        pass
    got = (request.headers.get("X-Internal-Secret") or "").strip()
    if not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="invalid X-Internal-Secret")
    body = await request.json()
    entries = body.get("entries") or []
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="entries must be a list")
    inserted = 0
    updated = 0
    skipped = []
    with connect() as db:
        for e in entries:
            if not isinstance(e, dict):
                continue
            email = (e.get("email") or "").strip().lower()
            token = (e.get("magic_token") or "").strip()
            batch = int(e.get("batch") or 1)
            if not email or not token:
                skipped.append({"email": email, "reason": "missing email or magic_token"})
                continue
            try:
                row = db.execute(
                    """
                    insert into registration_allowlist (email_norm, magic_token, batch, source)
                    values (%s, %s, %s, 'landing-batch')
                    on conflict (email_norm) do update
                      set magic_token = excluded.magic_token,
                          batch = excluded.batch
                    returning (xmax = 0) as is_new
                    """,
                    (email, token, batch),
                ).fetchone()
                if row and row.get("is_new"):
                    inserted += 1
                else:
                    updated += 1
            except Exception as exc:
                skipped.append({"email": email, "reason": f"{type(exc).__name__}: {exc}"})
    return json_response({
        "ok": True,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    })
