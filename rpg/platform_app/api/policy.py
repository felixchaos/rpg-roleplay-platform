"""platform_app.api.policy — 政策通知 API 端点 (DOC-02 / AUP-03).

用户端:
  GET  /api/policy/{slug}/status      公开查询某条政策当前版本与待更新信息
  GET  /api/policy/notices             已登录用户查询 pending 通知(供横幅使用)

管理端 (admin only):
  POST /api/admin/policy/notices                     创建政策变更通知
  POST /api/admin/policy/notices/{id}/dispatch       立即触发邮件发送
  POST /api/admin/policy/notices/{id}/activate       强制激活新版本
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from platform_app.db import connect
from platform_app.api._deps import current_user, require_user, json_response
from platform_app.policy_notice import (
    POLICY_SLUGS,
    activate_notice,
    dispatch_notice,
    get_current_version,
    list_pending_notices,
    schedule_policy_change,
)

router = APIRouter()
log = logging.getLogger(__name__)


# ─────────────────────────── guards ──────────────────────────────────────────

def _require_admin(request: Request):
    user = current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ─────────────────────────── user-facing ─────────────────────────────────────

@router.get("/api/policy/{slug}/status")
def policy_slug_status(slug: str):
    """查询某条政策当前版本与待变更信息（公开端点）。"""
    if slug not in POLICY_SLUGS:
        raise HTTPException(status_code=404, detail=f"未知政策 slug: {slug!r}")

    with connect() as db:
        current_version = get_current_version(db, slug)
        pending = [n for n in list_pending_notices(db) if n["slug"] == slug]

    pending_change = None
    if pending:
        latest = sorted(pending, key=lambda n: n["effective_at"])[-1]
        pending_change = {
            "new_version": latest["new_version"],
            "effective_at": latest["effective_at"],
            "summary": latest["summary"],
        }

    return json_response({
        "slug": slug,
        "current_version": current_version,
        "pending_change": pending_change,
    })


@router.get("/api/policy/notices")
def policy_notices_for_user(request: Request):
    """已登录用户查询 pending 通知(供前端横幅使用)。需要登录。"""
    user = require_user(request)

    with connect() as db:
        notices = list_pending_notices(db)

    # 只返回前端需要的字段
    result = [
        {
            "id": n["id"],
            "slug": n["slug"],
            "new_version": n["new_version"],
            "summary": n["summary"],
            "effective_at": n["effective_at"],
            "dispatched_at": n.get("dispatched_at"),
        }
        for n in notices
    ]
    return json_response({"notices": result})


# ─────────────────────────── admin-facing ────────────────────────────────────

class CreateNoticeBody(BaseModel):
    slug: str
    new_version: str
    summary: str
    effective_at: Optional[str] = None  # ISO-8601; 不传则默认 now+30d


@router.post("/api/admin/policy/notices")
def admin_create_notice(body: CreateNoticeBody, request: Request):
    """admin: 创建政策变更通知。"""
    _require_admin(request)

    if body.slug not in POLICY_SLUGS:
        raise HTTPException(status_code=400, detail=f"未知 slug: {body.slug!r}")

    effective_at = None
    if body.effective_at:
        try:
            effective_at = datetime.fromisoformat(body.effective_at)
            if effective_at.tzinfo is None:
                effective_at = effective_at.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="effective_at 格式无效,需 ISO-8601")

    with connect() as db:
        record = schedule_policy_change(
            db,
            slug=body.slug,
            new_version=body.new_version,
            summary=body.summary,
            effective_at=effective_at,
        )

    return json_response({"notice": record})


@router.post("/api/admin/policy/notices/{notice_id}/dispatch")
def admin_dispatch_notice(notice_id: str, request: Request):
    """admin: 立即触发邮件批量发送(补发 / 手动触发)。"""
    _require_admin(request)

    with connect() as db:
        try:
            record = dispatch_notice(db, notice_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return json_response({"notice": record})


@router.post("/api/admin/policy/notices/{notice_id}/activate")
def admin_activate_notice(notice_id: str, request: Request):
    """admin: 强制激活新版本(cron 到期后自动跑;此端点用于手动提前激活)。"""
    _require_admin(request)

    with connect() as db:
        try:
            record = activate_notice(db, notice_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return json_response({"notice": record})
