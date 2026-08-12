"""platform_app.api.admin.co_builders —— Beta Co-builders 名单端点(/api/admin/co-builders)。纯机械搬家,行为零变化。"""
from __future__ import annotations

from fastapi import Depends

from ...db import connect
from .._deps import json_response
from ._shared import _require_admin, router


@router.get("/api/admin/co-builders")
async def admin_co_builders(admin=Depends(_require_admin)):
    """列出「测试期共建者」名单：通过 magic link 注册且已完成账户设置的用户。

    只含 username != email 的用户（magic link 建账号时初值 username = email；
    用户补填昵称后 username 才与 email 不同，视作「已完成注册」）。
    opted_out=True 的条目仍包含在内，便于管理员审核，但发布时应过滤掉。
    """
    with connect() as db:
        rows = db.execute(
            """
            select u.id as user_id, u.username, u.display_name, u.email,
                   u.created_at as registered_at, u.co_builder_opt_out as opted_out
            from users u
            join registration_allowlist a on a.used_by_user_id = u.id
            where u.username is not null and u.username != u.email
            order by u.created_at
            """,
        ).fetchall()
    entries = [dict(r) for r in rows]
    return json_response({
        "ok": True,
        "count": len(entries),
        "entries": entries,
    })
