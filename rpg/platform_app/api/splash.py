"""platform_app.api.splash — AGE-02 成人内容声明 splash ack。

端点:
  GET  /api/me/splash/status  — 当前用户是否已 ack 当前版本
  POST /api/me/splash/ack     — 记录 ack
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import connect
from ._deps import _client_ip, json_response, require_user

router = APIRouter()

SPLASH_CURRENT_VERSION = "v1.0-2026-05-31"


@router.get("/api/me/splash/status")
async def api_splash_status(user=Depends(require_user), request: Request = None):
    """返回当前用户是否已 ack 当前 splash version。"""
    with connect() as db:
        row = db.execute(
            "select acked_at from splash_acks where user_id = %s and splash_version = %s",
            (user["id"], SPLASH_CURRENT_VERSION),
        ).fetchone()
    return json_response(
        {
            "ok": True,
            "current_version": SPLASH_CURRENT_VERSION,
            "acked": row is not None,
            "acked_at": row["acked_at"].isoformat() if row else None,
        }
    )


@router.post("/api/me/splash/ack")
async def api_splash_ack(request: Request, user=Depends(require_user)):
    """记录用户对当前 splash 版本的 ack。"""
    body = await request.json()
    ver = body.get("splash_version") or SPLASH_CURRENT_VERSION
    if ver != SPLASH_CURRENT_VERSION:
        raise HTTPException(400, detail="stale splash version")
    ip = _client_ip(request)
    with connect() as db:
        bd_row = db.execute(
            "select birthday from users where id = %s", (user["id"],)
        ).fetchone()
        dob = bd_row["birthday"] if bd_row else None
        db.execute(
            "insert into splash_acks(user_id, splash_version, acked_at, dob_confirmed, ip) "
            "values (%s, %s, now(), %s, %s) on conflict do nothing",
            (user["id"], ver, dob, ip),
        )
    return json_response({"ok": True, "splash_version": ver})
