"""platform_app.api.admin.maintenance —— 2.9 维护模式 + 2.10 服务重启端点。纯机械搬家,行为零变化。

_MAINTENANCE_CFG_KEY / _DEFAULT_MAINTENANCE 仅本资源族读写,故与端点同居(单一读写方)。
"""
from __future__ import annotations

import os
import signal
from datetime import UTC, datetime

from fastapi import Depends, Request

from ...db import connect
from .._deps import _client_ip, json_response
from ._shared import _get_app_config, _require_admin, _set_app_config, _write_audit, router

_MAINTENANCE_CFG_KEY = "admin.maintenance_config"

_DEFAULT_MAINTENANCE = {
    "maintenance_mode": False,
    "announcement": "",
    "maintenance_since": None,
}


@router.get("/api/admin/maintenance")
async def admin_get_maintenance(admin=Depends(_require_admin)):
    with connect() as db:
        cfg = _get_app_config(db, _MAINTENANCE_CFG_KEY)
    merged = {**_DEFAULT_MAINTENANCE, **cfg}
    return json_response(merged)


@router.post("/api/admin/maintenance")
async def admin_set_maintenance(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    update: dict = {}
    if "maintenance_mode" in body:
        update["maintenance_mode"] = bool(body["maintenance_mode"])
        if update["maintenance_mode"]:
            update["maintenance_since"] = datetime.now(UTC).isoformat()
        else:
            update["maintenance_since"] = None
    if "announcement" in body:
        update["announcement"] = str(body["announcement"])

    with connect() as db:
        _set_app_config(db, _MAINTENANCE_CFG_KEY, update)
        _write_audit(db, admin, "maintenance.toggle", details=update, ip=ip)

    return json_response({"ok": True})


@router.post("/api/admin/restart")
async def admin_restart(
    request: Request,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        _write_audit(db, admin, "system.restart", details={}, ip=ip)

    os.kill(os.getpid(), signal.SIGHUP)
    return json_response({
        "ok": True,
        "message": "重启信号已发送，服务将在当前请求完成后重载",
    })
