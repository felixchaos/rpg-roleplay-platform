"""platform_app.api.admin.users —— 2.2 用户管理端点(list/update/deactivate/reactivate/force-logout)。纯机械搬家,行为零变化。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ...db import connect, limit_value
from .._deps import _client_ip, json_response
from ._shared import _require_admin, _write_audit, router


@router.get("/api/admin/users")
async def admin_list_users(
    request: Request,
    page: int = 1,
    limit: int = 20,
    search: str = "",
    role: str = "all",
    status: str = "all",
    admin=Depends(_require_admin),
):
    page = max(1, page)
    limit = limit_value(limit, default=20, maximum=100)
    offset = (page - 1) * limit
    search_pat = f"%{search}%" if search else ""

    with connect() as db:
        count_row = db.execute(
            """
            select count(*) as total from users u
            where (%s = '' or u.username ilike %s or u.display_name ilike %s)
              and (%s = 'all' or u.role = %s)
              and (%s = 'all'
                   or (%s = 'active' and u.deactivated_at is null)
                   or (%s = 'deactivated' and u.deactivated_at is not null))
            """,
            (search_pat, search_pat, search_pat, role, role, status, status, status),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = db.execute(
            """
            select
              u.id, u.username, u.display_name, u.role, u.bio,
              u.created_at, u.deactivated_at,
              coalesce(u.ban_reason, '') as ban_reason,
              (select la.created_at from login_audit la
               where la.username = u.username and la.event = 'login_ok'
               order by la.created_at desc limit 1) as last_login_at,
              (select count(*) from sessions s
               where s.user_id = u.id and s.expires_at > now()) as session_count,
              coalesce((select sum(tu.total_tokens) from token_usage tu
               where tu.user_id = u.id
                 and tu.created_at > now() - interval '30 days'), 0) as usage_tokens_30d
            from users u
            where (%s = '' or u.username ilike %s or u.display_name ilike %s)
              and (%s = 'all' or u.role = %s)
              and (%s = 'all'
                   or (%s = 'active' and u.deactivated_at is null)
                   or (%s = 'deactivated' and u.deactivated_at is not null))
            order by u.created_at desc
            limit %s offset %s
            """,
            (
                search_pat, search_pat, search_pat,
                role, role,
                status, status, status,
                limit, offset,
            ),
        ).fetchall()

    return json_response({
        "users": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
    })


@router.patch("/api/admin/users/{user_id}")
async def admin_update_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    new_role = body.get("role")
    ban_reason = body.get("ban_reason")
    display_name = body.get("display_name")

    # 禁止管理员降级自己
    if new_role == "user" and admin.get("id") == user_id:
        raise HTTPException(status_code=400, detail="不允许将自己降级为普通用户")

    with connect() as db:
        if not db.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone():
            raise HTTPException(status_code=404, detail="用户不存在")
        if new_role is not None:
            # task: vip_user 享受平台 RAG embedder 兜底,但仍 LLM BYOK,跟 admin 区别
            # 是 vip_user 没 admin 权限(不能改其他用户、看 audit log 等)
            if new_role not in ("user", "vip_user", "admin"):
                raise HTTPException(status_code=400, detail="role 只能是 user / vip_user / admin")
            db.execute("update users set role = %s where id = %s", (new_role, user_id))
            _write_audit(db, admin, "user.update_role",
                         target_type="user", target_id=str(user_id),
                         details={"role": new_role}, ip=ip)

        updates = []
        params = []
        if ban_reason is not None:
            updates.append("ban_reason = %s")
            params.append(ban_reason)
        if display_name is not None:
            updates.append("display_name = %s")
            params.append(display_name)

        if updates:
            params.append(user_id)
            db.execute(
                f"update users set {', '.join(updates)} where id = %s",
                params,
            )
            _write_audit(db, admin, "user.update_info",
                         target_type="user", target_id=str(user_id),
                         details={k: v for k, v in body.items() if k != "role"}, ip=ip)

    return json_response({"ok": True})


@router.post("/api/admin/users/{user_id}/deactivate")
async def admin_deactivate_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    # 禁止管理员停用自己:deactivated_at 让自己的 token 立即失效 + 删自己的 session →
    # 自锁在门外且无法撤销(已停用进不了 admin)。与 update_role 的自降级保护一致。
    if admin.get("id") == user_id:
        raise HTTPException(status_code=400, detail="不允许停用自己的账户")
    with connect() as db:
        db.execute(
            "update users set deactivated_at = now() where id = %s",
            (user_id,),
        )
        result = db.execute(
            "delete from sessions where user_id = %s returning token",
            (user_id,),
        ).fetchall()
        sessions_revoked = len(result)
        _write_audit(db, admin, "user.deactivate",
                     target_type="user", target_id=str(user_id),
                     details={"sessions_revoked": sessions_revoked}, ip=ip)

    return json_response({"ok": True, "sessions_revoked": sessions_revoked})


@router.post("/api/admin/users/{user_id}/reactivate")
async def admin_reactivate_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        db.execute(
            "update users set deactivated_at = null, ban_reason = '' where id = %s",
            (user_id,),
        )
        _write_audit(db, admin, "user.reactivate",
                     target_type="user", target_id=str(user_id),
                     details={}, ip=ip)

    return json_response({"ok": True})


@router.post("/api/admin/users/{user_id}/force-logout")
async def admin_force_logout_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        result = db.execute(
            "delete from sessions where user_id = %s returning token",
            (user_id,),
        ).fetchall()
        sessions_revoked = len(result)
        _write_audit(db, admin, "user.force_logout",
                     target_type="user", target_id=str(user_id),
                     details={"sessions_revoked": sessions_revoked}, ip=ip)

    return json_response({"ok": True, "sessions_revoked": sessions_revoked})
