"""platform_app.api.feedback — FB-01/02/03/07/08 反馈提交与管理接口。

路由:
  POST   /api/feedback                        — 用户提交反馈 (FB-01)
  GET    /api/me/feedback                     — 用户查看自己的反馈列表 (FB-07)
  DELETE /api/feedback/{id}                   — 用户撤回单条 unreviewed (FB-08)
  POST   /api/me/feedback/delete-all          — 用户撤销所有 (FB-08)
  GET    /api/admin/feedback                  — admin 审查队列 (FB-03)
  POST   /api/admin/feedback/{id}/decision    — admin 标记 ok|nsfw_terminate|spam (FB-03)

consent_token 设计:
  前端把当时展示给用户的同意文案做 SHA256 (hex)，随请求带上。
  服务端只做长度/格式校验后存入 feedback 行，供后续合规 audit 比对。
  不在服务端重算文案——这样文案升版本时历史 token 仍可追溯。
"""
from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import connect
from ._deps import _client_ip, json_response, require_user

router = APIRouter()
log = logging.getLogger(__name__)

# 50 KB (free_text + excerpts JSON 合计)
_MAX_PAYLOAD_BYTES = 50 * 1024
_VALID_DECISIONS = {"ok", "nsfw_terminate", "spam"}


def _require_admin(user=Depends(require_user)):
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/feedback — 用户提交
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/feedback")
async def submit_feedback(request: Request, user=Depends(require_user)):
    """FB-01/02: 提交反馈 + 写 consent_log。"""
    body = await request.json()
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")

    free_text: str = body.get("free_text", "") or ""
    excerpts = body.get("excerpts", []) or []
    consent_token: str = body.get("consent_token", "") or ""
    app_version: str = body.get("app_version", "") or ""

    # ── 校验 consent_token（SHA256 hex，64 字符）──────────────────────────────
    if not consent_token or len(consent_token) != 64:
        raise HTTPException(
            status_code=400,
            detail="consent_token 缺失或格式不正确（须为 64 字符 SHA256 hex）",
        )
    try:
        int(consent_token, 16)
    except ValueError:
        raise HTTPException(status_code=400, detail="consent_token 不是合法的 hex 字符串")

    # ── 校验总长 50KB ─────────────────────────────────────────────────────────
    excerpts_raw = json.dumps(excerpts, ensure_ascii=False)
    total_bytes = len(free_text.encode("utf-8")) + len(excerpts_raw.encode("utf-8"))
    if total_bytes > _MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"free_text + excerpts 超过 50KB 上限（当前 {total_bytes} 字节）",
        )

    # ── excerpts 结构简单校验 ──────────────────────────────────────────────────
    if not isinstance(excerpts, list):
        raise HTTPException(status_code=400, detail="excerpts 须为数组")
    for i, ex in enumerate(excerpts):
        if not isinstance(ex, dict):
            raise HTTPException(status_code=400, detail=f"excerpts[{i}] 须为对象")

    with connect() as db:
        # 写 feedback 行
        row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip)
            values (%s, %s, %s::jsonb, %s, %s, %s, %s)
            returning id
            """,
            (
                user["id"],
                free_text,
                excerpts_raw,
                consent_token,
                ua,
                app_version,
                ip,
            ),
        ).fetchone()
        feedback_id = row["id"]

        # 写 feedback_consent_log 行（供 audit；即便 feedback 日后被删，此行保留）
        db.execute(
            """
            insert into feedback_consent_log
              (user_id, consent_text_hash, app_version, ip)
            values (%s, %s, %s, %s)
            """,
            (user["id"], consent_token, app_version, ip),
        )

    log.info("feedback submitted: id=%s user_id=%s", feedback_id, user["id"])
    return json_response({"ok": True, "feedback_id": feedback_id})


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/me/feedback — 用户查看自己的反馈
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/me/feedback")
async def list_my_feedback(
    limit: int = 20,
    user=Depends(require_user),
):
    """FB-07: 用户查看自己的历史反馈（含状态）。"""
    limit = max(1, min(100, limit))
    with connect() as db:
        rows = db.execute(
            """
            select id, free_text, review_decision, reviewed_at, created_at
            from feedback
            where user_id = %s
            order by created_at desc
            limit %s
            """,
            (user["id"], limit),
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "free_text_preview": (r["free_text"] or "")[:100],
            "review_decision": r["review_decision"],
            "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return json_response({"ok": True, "items": items})


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /api/feedback/{id} — 用户撤回单条
# ──────────────────────────────────────────────────────────────────────────────

@router.delete("/api/feedback/{feedback_id}")
async def delete_my_feedback(
    feedback_id: int,
    user=Depends(require_user),
):
    """FB-08: 用户撤回单条 unreviewed 反馈。
    已被 nsfw_terminate 标记的不允许删除（403）。
    consent_log 行保留。
    """
    with connect() as db:
        row = db.execute(
            "select user_id, review_decision from feedback where id = %s",
            (feedback_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        if row["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="无权操作此反馈")
        if row["review_decision"] == "nsfw_terminate":
            raise HTTPException(
                status_code=403,
                detail="该反馈已被标记为 nsfw_terminate，根据 AUP §2.J 不允许删除（合规证据保留）",
            )

        db.execute("delete from feedback where id = %s", (feedback_id,))

    return json_response({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/me/feedback/delete-all — 用户撤销所有
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/me/feedback/delete-all")
async def delete_all_my_feedback(user=Depends(require_user)):
    """FB-08: 用户一键撤回所有未被 nsfw_terminate 标记的反馈。
    nsfw_terminate 行保留（合规证据）。consent_log 保留。
    """
    with connect() as db:
        cur = db.execute(
            """
            delete from feedback
            where user_id = %s
              and (review_decision is null or review_decision != 'nsfw_terminate')
            """,
            (user["id"],),
        )
        deleted = cur.rowcount

    return json_response({"ok": True, "deleted": deleted})


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/admin/feedback — admin 审查队列 (FB-03)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/feedback")
async def admin_list_feedback(
    status: str = "unreviewed",
    limit: int = 50,
    admin=Depends(_require_admin),
):
    """FB-03: admin 查看反馈审查队列。status=unreviewed|reviewed|all"""
    limit = max(1, min(200, limit))
    with connect() as db:
        rows = db.execute(
            """
            select f.id, f.user_id, u.username,
                   f.free_text, f.excerpts_jsonb,
                   f.review_decision, f.reviewed_at,
                   f.app_version, f.created_at
            from feedback f
            left join users u on u.id = f.user_id
            where (
              %s = 'all'
              or (%s = 'unreviewed' and f.review_decision is null)
              or (%s = 'reviewed' and f.review_decision is not null)
            )
            order by f.created_at desc
            limit %s
            """,
            (status, status, status, limit),
        ).fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "user_id": r["user_id"],
            "username": r["username"] or "—",
            "free_text": r["free_text"] or "",
            "excerpts": r["excerpts_jsonb"] if r["excerpts_jsonb"] else [],
            "review_decision": r["review_decision"],
            "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            "app_version": r["app_version"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return json_response({"ok": True, "items": items})


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/admin/feedback/{id}/decision — admin 审查决定 (FB-03)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/feedback/{feedback_id}/decision")
async def admin_feedback_decision(
    request: Request,
    feedback_id: int,
    admin=Depends(_require_admin),
):
    """FB-03: admin 标记反馈。
    decision=ok|nsfw_terminate|spam
    nsfw_terminate 时走现有 /api/admin/users/{id}/terminate 逻辑。
    """
    body = await request.json()
    ip = _client_ip(request)
    decision = body.get("decision", "")
    notes = body.get("notes", "") or ""

    if decision not in _VALID_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"decision 须为 {' | '.join(_VALID_DECISIONS)}",
        )

    with connect() as db:
        row = db.execute(
            "select id, user_id from feedback where id = %s",
            (feedback_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")

        db.execute(
            """
            update feedback
            set review_decision = %s, reviewed_at = now()
            where id = %s
            """,
            (decision, feedback_id),
        )

        # nsfw_terminate: 调现有 queue_account_termination
        if decision == "nsfw_terminate":
            from ..dmca import queue_account_termination
            terminate_reason = f"反馈审查 nsfw_terminate (feedback_id={feedback_id}): {notes}"
            queue_account_termination(db, row["user_id"], terminate_reason)
            log.warning(
                "feedback nsfw_terminate: feedback_id=%s user_id=%s admin=%s",
                feedback_id, row["user_id"], admin.get("username"),
            )

    return json_response({"ok": True, "decision": decision})
