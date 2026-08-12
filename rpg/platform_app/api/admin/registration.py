"""platform_app.api.admin.registration —— 2.7 注册与邀请端点(registration 配置 + invite-codes CRUD)。纯机械搬家,行为零变化。

_REGISTRATION_CFG_KEY / _DEFAULT_REGISTRATION 仅本资源族读写,故与端点同居(单一读写方)。
"""
from __future__ import annotations

import secrets
import string

from fastapi import Depends, HTTPException, Request

from ...db import connect
from .._deps import _client_ip, json_response
from ._shared import _get_app_config, _require_admin, _set_app_config, _write_audit, router

_REGISTRATION_CFG_KEY = "admin.registration_config"

_DEFAULT_REGISTRATION = {
    "mode": "open",
    "require_email_verify": False,
    "auto_approve": True,
}


@router.get("/api/admin/registration")
async def admin_get_registration(admin=Depends(_require_admin)):
    with connect() as db:
        cfg = _get_app_config(db, _REGISTRATION_CFG_KEY)
    merged = {**_DEFAULT_REGISTRATION, **cfg}
    return json_response(merged)


@router.post("/api/admin/registration")
async def admin_set_registration(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    allowed_keys = {"mode", "require_email_verify", "auto_approve"}
    update = {k: v for k, v in body.items() if k in allowed_keys}

    with connect() as db:
        _set_app_config(db, _REGISTRATION_CFG_KEY, update)
        _write_audit(db, admin, "config.registration",
                     details=update, ip=ip)

    return json_response({"ok": True})


@router.get("/api/admin/invite-codes")
async def admin_list_invite_codes(
    page: int = 1,
    limit: int = 50,
    used: str = "all",
    admin=Depends(_require_admin),
):
    page = max(1, page)
    limit = max(1, min(200, limit))
    offset = (page - 1) * limit

    with connect() as db:
        count_row = db.execute(
            """
            select count(*) as total from invite_codes
            where (%s = 'all'
                   or (%s = 'used' and used_by is not null)
                   or (%s = 'unused' and used_by is null))
            """,
            (used, used, used),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = db.execute(
            """
            select ic.id, ic.code, ic.note, ic.expires_at, ic.used_at, ic.created_at,
                   u.username as used_by_username
            from invite_codes ic
            left join users u on u.id = ic.used_by
            where (%s = 'all'
                   or (%s = 'used' and ic.used_by is not null)
                   or (%s = 'unused' and ic.used_by is null))
            order by ic.created_at desc
            limit %s offset %s
            """,
            (used, used, used, limit, offset),
        ).fetchall()

    return json_response({
        "codes": [dict(r) for r in rows],
        "total": total,
    })


@router.post("/api/admin/invite-codes")
async def admin_create_invite_codes(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    count = max(1, min(20, int(body.get("count", 1))))
    expires_in_days = body.get("expires_in_days")
    note = body.get("note", "")

    alphabet = string.ascii_uppercase + string.digits
    created = []

    with connect() as db:
        for _ in range(count):
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            expires_at = None
            if expires_in_days is not None:
                db.execute(
                    """
                    insert into invite_codes(code, created_by, expires_at, note)
                    values(%s, %s, now() + (%s || ' days')::interval, %s)
                    returning code, expires_at, created_at
                    """,
                    (code, admin.get("id"), str(int(expires_in_days)), note),
                )
            else:
                db.execute(
                    """
                    insert into invite_codes(code, created_by, note)
                    values(%s, %s, %s)
                    returning code, expires_at, created_at
                    """,
                    (code, admin.get("id"), note),
                )
            row = db.execute(
                "select code, expires_at, created_at from invite_codes where code = %s",
                (code,),
            ).fetchone()
            if row:
                created.append(dict(row))

        _write_audit(db, admin, "invite.create",
                     details={"count": count, "expires_in_days": expires_in_days, "note": note},
                     ip=ip)

    return json_response({"codes": created})


@router.post("/api/admin/invite-codes/{code}/delete")
async def admin_delete_invite_code(
    request: Request,
    code: str,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        result = db.execute(
            "delete from invite_codes where code = %s and used_by is null returning id",
            (code,),
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="邀请码不存在或已被使用")
        _write_audit(db, admin, "invite.delete",
                     target_type="invite_code", target_id=code,
                     details={}, ip=ip)

    return json_response({"ok": True})
