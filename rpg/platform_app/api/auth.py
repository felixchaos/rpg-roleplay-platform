"""platform_app.api.auth — /api/auth/* 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

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
    require_user,
)

router = APIRouter()


# 保留 request：register/login/logout 是认证类 endpoint，本身处理 cookie/IP
@router.post("/api/auth/register")
async def api_register(request: Request):
    body = await request.json()
    ip = _client_ip(request)
    from ..security import normalize_username
    normalized_username = normalize_username(body.get("username", ""))
    # IP 速率限制：复用登录的速率限制，防止枚举/暴力注册
    try:
        _auth._check_rate_limit(ip, normalized_username)
    except _auth.RateLimited as rl:
        return json_response(
            {"ok": False, "error": f"请求频率过高，请 {rl.retry_after_sec} 秒后再试"},
            status_code=429,
            headers={"Retry-After": str(rl.retry_after_sec)},
        )
    # 首管理员引导令牌:body.setup_token 优先,其次 X-Setup-Token 头(server 模式才生效)
    setup_token = body.get("setup_token") or request.headers.get("X-Setup-Token")
    try:
        user = _auth.register(
            body.get("username", ""),
            body.get("password", ""),
            body.get("display_name", ""),
            setup_token=setup_token,
        )
        workspace.ensure_default(user["id"])
        user, token = _auth.login(body.get("username", ""), body.get("password", ""))
        response = json_response({"ok": True, "user": public_user(user), "platform": platform_for(user)})
        _set_session_cookie(response, request, token)
        return response
    except ValueError:
        # 模糊提示，避免用户名枚举（不区分"用户名已存在"与其它注册失败）
        _auth._record_login_fail(ip, normalized_username)
        return json_response({"ok": False, "error": "注册失败，请检查输入后重试"}, status_code=400)


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


def _require_admin(user=Depends(require_user)):
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@router.post("/api/admin/login/unlock")
async def api_admin_login_unlock(request: Request, admin=Depends(_require_admin)):
    """管理员手动解除某个用户/IP 的登录锁定。
    body: { username?: str, ip?: str }  — 二选一,或同时传。
    """
    body = await request.json()
    username = (body.get("username") or "").strip()
    ip = (body.get("ip") or "").strip()
    if not username and not ip:
        return json_response({"ok": False, "error": "username 或 ip 至少传一个"}, status_code=400)
    _auth.admin_unlock(ip=ip, username=username)
    return json_response({"ok": True, "unlocked": {"username": username or None, "ip": ip or None}})


@router.get("/api/auth/schema")
async def api_auth_schema():
    """登录/注册表单的字段定义,前端 login-app.jsx 据此动态渲染。

    返回结构 (前端直接 setSchema(j),按 schema[mode] 取字段数组):
      { login: [...], register: [...], notes: {...} }
    字段属性: key / label / type / required / min_length。
    后端是字段的唯一权威源 — 加减字段只改这里,前端零改动。
    """
    pw_min = _auth.MIN_PASSWORD_LENGTH
    from ..db import connect, init_db
    init_db()
    with connect() as db:
        user_count = db.execute("select count(*) as n from users").fetchone()["n"]
    first_user_is_admin = int(user_count) == 0
    return json_response({
        "login": [
            {"key": "username", "label": "用户名", "type": "text", "required": True},
            {"key": "password", "label": "密码", "type": "password", "required": True, "min_length": pw_min},
        ],
        "register": [
            {"key": "username", "label": "用户名", "type": "text", "required": True},
            {"key": "display_name", "label": "昵称(可选)", "type": "text", "required": False},
            {"key": "password", "label": "密码", "type": "password", "required": True, "min_length": pw_min},
        ],
        "notes": {
            "min_password_length": pw_min,
            "max_password_length": 1024,
            "invite_only": False,
            "first_user_is_admin": first_user_is_admin,
        },
    })
