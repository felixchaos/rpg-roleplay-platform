"""platform_app.api.admin.security —— 2.8 安全配置端点(security-config 读/写)。纯机械搬家,行为零变化。

_SECURITY_CFG_KEY / _DEFAULT_SECURITY 仅本资源族读写,故与端点同居(单一读写方)。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from .._deps import _client_ip, json_response
from ._shared import _get_app_config, _require_admin, _set_app_config, _write_audit, router

_SECURITY_CFG_KEY = "admin.security_config"

_DEFAULT_SECURITY = {
    "ip_blocklist": [],
    "rate_limit_per_ip": 30,
    "rate_limit_per_user": 10,
    "rate_window_minutes": 10,
    "password_min_length": 6,
    "password_require_numbers": False,
    "session_timeout_days": 14,
    "login_lock_threshold": 10,
    "login_lock_duration_min": 30,
}


@router.get("/api/admin/security-config")
async def admin_get_security_config(admin=Depends(_require_admin)):
    with connect() as db:
        cfg = _get_app_config(db, _SECURITY_CFG_KEY)
    merged = {**_DEFAULT_SECURITY, **cfg}
    return json_response(merged)


@router.post("/api/admin/security-config")
async def admin_set_security_config(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    allowed_keys = set(_DEFAULT_SECURITY.keys())
    update = {k: v for k, v in body.items() if k in allowed_keys}

    with connect() as db:
        _set_app_config(db, _SECURITY_CFG_KEY, update)
        _write_audit(db, admin, "config.security", details=update, ip=ip)

    return json_response({"ok": True})
