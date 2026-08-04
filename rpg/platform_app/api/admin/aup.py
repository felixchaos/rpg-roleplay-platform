"""platform_app.api.admin.aup —— 3.4 AUP 账户暂停 / 解封 / 终止端点。纯机械搬家,行为零变化。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ...db import connect
from ...dmca import queue_account_termination
from .._deps import _client_ip, json_response
from ._shared import _require_admin, _write_audit, router


@router.post("/api/admin/users/{user_id}/suspend")
async def admin_suspend_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)
    reason = body.get("reason", "")
    duration_days = body.get("duration_days")  # None = 无限期

    if not reason:
        raise HTTPException(status_code=400, detail="reason 不能为空")

    suspend_until = None
    if duration_days is not None:
        try:
            duration_days = int(duration_days)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="duration_days 须为整数")

    with connect() as db:
        if duration_days is not None:
            db.execute(
                """
                update users
                set deactivated_at = now(),
                    ban_reason = %s,
                    suspended_until = now() + (%s || ' days')::interval
                where id = %s
                """,
                (reason, str(duration_days), user_id),
            )
        else:
            db.execute(
                "update users set deactivated_at = now(), ban_reason = %s where id = %s",
                (reason, user_id),
            )
        # 撤销所有活跃 Session
        result = db.execute(
            "delete from sessions where user_id = %s returning token",
            (user_id,),
        ).fetchall()
        sessions_revoked = len(result)
        _write_audit(db, admin, "aup.suspend",
                     target_type="user", target_id=str(user_id),
                     details={"reason": reason, "duration_days": duration_days,
                               "sessions_revoked": sessions_revoked}, ip=ip)

    return json_response({"ok": True, "sessions_revoked": sessions_revoked})


@router.post("/api/admin/users/{user_id}/unsuspend")
async def admin_unsuspend_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        db.execute(
            """
            update users
            set deactivated_at = null, ban_reason = '', suspended_until = null
            where id = %s
            """,
            (user_id,),
        )
        _write_audit(db, admin, "aup.unsuspend",
                     target_type="user", target_id=str(user_id),
                     details={}, ip=ip)

    return json_response({"ok": True})


@router.post("/api/admin/users/{user_id}/terminate")
async def admin_terminate_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    """永久终止账户：写 banned_users + account_delete_queue，撤销所有 Session。"""
    body = await request.json()
    ip = _client_ip(request)
    reason = body.get("reason", "")

    if not reason:
        raise HTTPException(status_code=400, detail="reason 不能为空")

    # 管理员不能终止自己
    if admin.get("id") == user_id:
        raise HTTPException(status_code=400, detail="不允许终止自己的账户")

    with connect() as db:
        queue_account_termination(db, user_id, reason)
        _write_audit(db, admin, "aup.terminate",
                     target_type="user", target_id=str(user_id),
                     details={"reason": reason}, ip=ip)
    return json_response({"ok": True})  # SEC(L-6): 显式返回,避免 200 空 body 致前端误判/重试
