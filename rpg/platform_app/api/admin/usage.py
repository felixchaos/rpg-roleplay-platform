"""platform_app.api.admin.usage —— 2.3 全局用量端点(/api/admin/usage)。纯机械搬家,行为零变化。"""
from __future__ import annotations

from fastapi import Depends

from ...db import connect
from .._deps import json_response
from ._shared import _require_admin, router


@router.get("/api/admin/usage")
async def admin_usage(
    days: int = 30,
    admin=Depends(_require_admin),
):
    days = max(1, min(365, days))

    with connect() as db:
        total_row = db.execute(
            """
            select
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage
            where created_at > now() - (%s || ' days')::interval
            """,
            (str(days),),
        ).fetchone()

        by_user = db.execute(
            """
            select
              tu.user_id,
              u.username,
              u.display_name,
              coalesce(sum(tu.input_tokens), 0) as input_tokens,
              coalesce(sum(tu.output_tokens), 0) as output_tokens,
              coalesce(sum(tu.total_tokens), 0) as total_tokens,
              coalesce(sum(tu.cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage tu
            join users u on u.id = tu.user_id
            where tu.created_at > now() - (%s || ' days')::interval
            group by tu.user_id, u.username, u.display_name
            order by cost_usd desc
            limit 20
            """,
            (str(days),),
        ).fetchall()

        by_api = db.execute(
            """
            select
              api_id,
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage
            where created_at > now() - (%s || ' days')::interval
            group by api_id
            order by cost_usd desc
            """,
            (str(days),),
        ).fetchall()

        by_day = db.execute(
            """
            select
              date_trunc('day', created_at)::date as date,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage
            where created_at > now() - (%s || ' days')::interval
            group by 1
            order by 1 asc
            """,
            (str(days),),
        ).fetchall()

    return json_response({
        "total": dict(total_row) if total_row else {
            "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "cost_usd": 0, "requests": 0,
        },
        "by_user": [dict(r) for r in by_user],
        "by_api": [dict(r) for r in by_api],
        "by_day": [dict(r) for r in by_day],
    })
