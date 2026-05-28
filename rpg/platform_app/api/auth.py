"""platform_app.api.auth — /api/auth/* 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import auth as _auth
from .. import workspace
from ..security import public_user
from ._deps import (
    SESSION_COOKIE,
    _client_ip,
    _delete_session_cookie,
    _set_session_cookie,
    current_user,
    json_response,
    platform_for,
)

router = APIRouter()


# 保留 request：register/login/logout 是认证类 endpoint，本身处理 cookie/IP
@router.post("/api/auth/register")
async def api_register(request: Request):
    body = await request.json()
    try:
        user = _auth.register(body.get("username", ""), body.get("password", ""), body.get("display_name", ""))
        workspace.ensure_default(user["id"])
        user, token = _auth.login(body.get("username", ""), body.get("password", ""))
        response = json_response({"ok": True, "user": public_user(user), "platform": platform_for(user)})
        _set_session_cookie(response, request, token)
        return response
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# 保留 request：login 需要 _client_ip(request) 用于速率限制
@router.post("/api/auth/login")
async def api_login(request: Request):
    body = await request.json()
    ip = _client_ip(request)
    try:
        user, token = _auth.login(body.get("username", ""), body.get("password", ""), ip=ip)
        workspace.ensure_default(user["id"])
        response = json_response({"ok": True, "user": public_user(user), "platform": platform_for(user)})
        _set_session_cookie(response, request, token)
        return response
    except _auth.RateLimited as rl:
        return json_response(
            {"ok": False, "error": f"登录失败次数过多，请 {rl.retry_after_sec} 秒后再试"},
            status_code=429,
            headers={"Retry-After": str(rl.retry_after_sec)},
        )
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# 保留 request：logout 需要读 cookies 并设置 delete_cookie
@router.post("/api/auth/logout")
async def api_logout(request: Request):
    _auth.logout(request.cookies.get(SESSION_COOKIE))
    response = json_response({"ok": True})
    # 必须用跟 set 一致的 samesite/secure,否则跨域场景下浏览器会把 delete 当
    # "另一个 cookie" 残留,导致 SameSite=None 的 session cookie 还在(或反之)。
    _delete_session_cookie(response, request)
    return response


@router.get("/api/auth/me")
async def api_me(user=Depends(current_user)):
    # 安全：未登录不返回 DB 细节，仅返回 driver/ok 健康标识
    is_admin = bool(user and user.get("role") == "admin")
    from ..db import status as db_status
    return json_response({
        "ok": True,
        "user": public_user(user) if user else None,
        "database": db_status(reveal_details=is_admin),
    })
