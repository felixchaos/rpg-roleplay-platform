"""platform_app.api.admin.audit —— 2.4 审计日志端点(/api/admin/audit)。纯机械搬家,行为零变化。"""
from __future__ import annotations

from fastapi import Depends

from ...db import connect, limit_value
from .._deps import json_response
from ._shared import _require_admin, router


@router.get("/api/admin/audit")
async def admin_audit_log(
    page: int = 1,
    limit: int = 50,
    action_type: str = "",
    admin=Depends(_require_admin),
):
    page = max(1, page)
    limit = limit_value(limit, default=50, maximum=200)
    offset = (page - 1) * limit

    with connect() as db:
        count_row = db.execute(
            """
            select count(*) as total from admin_audit_log
            where (%s = '' or action like %s)
            """,
            (action_type, f"{action_type}%"),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = db.execute(
            """
            select id, actor_username, action, target_type, target_id, details, ip, created_at
            from admin_audit_log
            where (%s = '' or action like %s)
            order by created_at desc
            limit %s offset %s
            """,
            (action_type, f"{action_type}%", limit, offset),
        ).fetchall()

    return json_response({
        "entries": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
    })
