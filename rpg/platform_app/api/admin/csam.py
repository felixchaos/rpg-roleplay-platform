"""platform_app.api.admin.csam —— 3.3 CSAM 举报管理端点。纯机械搬家,行为零变化。

依赖表 csam_reports(v37 迁移创建)。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ...db import connect
from .._deps import _client_ip, json_response
from ._shared import _require_admin, _write_audit, router


@router.get("/api/admin/csam/reports")
async def admin_csam_list(
    status: str = "pending",
    limit: int = 50,
    admin=Depends(_require_admin),
):
    limit = max(1, min(200, limit))
    with connect() as db:
        rows = db.execute(
            """
            select r.id, r.reporter_id, r.reported_user_id,
                   r.content_url, r.description, r.status,
                   r.decision, r.decision_notes, r.cybertip_report_id,
                   r.created_at, r.decided_at, r.decided_by,
                   u.username as reported_username
            from csam_reports r
            left join users u on u.id = r.reported_user_id
            where (%s = 'all' or r.status = %s)
            order by r.created_at desc
            limit %s
            """,
            (status, status, limit),
        ).fetchall()
    return json_response({"reports": [dict(r) for r in rows]})


@router.post("/api/admin/csam/reports/{report_id}/decision")
async def admin_csam_decision(
    request: Request,
    report_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)
    decision = body.get("decision")
    notes = body.get("notes", "")

    if decision not in ("founded", "unfounded", "escalate"):
        raise HTTPException(status_code=400, detail="decision 须为 founded|unfounded|escalate")

    with connect() as db:
        result = db.execute(
            """
            update csam_reports
            set decision = %s, decision_notes = %s, status = %s,
                decided_at = now(), decided_by = %s
            where id = %s
            returning id, reported_user_id
            """,
            (decision, notes, "decided", admin.get("id"), report_id),
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="举报记录不存在")

        _write_audit(db, admin, f"csam.decision.{decision}",
                     target_type="csam_report", target_id=str(report_id),
                     details={"decision": decision, "notes": notes}, ip=ip)

    return json_response({"ok": True, "decision": decision})
