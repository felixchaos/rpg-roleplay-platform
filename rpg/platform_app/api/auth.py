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
    """Phase 1 注册：校验字段 → 发验证码 → 返回 pending_verify。不创建 users 行。"""
    body = await request.json()
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
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
    # 合规校验：服务条款 + 年龄确认
    terms_accepted = bool(body.get("terms_accepted"))
    age_confirmed = bool(body.get("age_confirmed"))
    if not terms_accepted:
        raise HTTPException(400, detail={"error_key": "auth.terms_not_accepted", "message": "请阅读并同意《服务条款》和《隐私政策》"})
    if not age_confirmed:
        raise HTTPException(400, detail={"error_key": "auth.age_not_confirmed", "message": "请确认你已年满 18 周岁"})
    # 首管理员引导令牌:body.setup_token 优先,其次 X-Setup-Token 头(server 模式才生效)
    setup_token = body.get("setup_token") or request.headers.get("X-Setup-Token")
    try:
        result = _auth.register(
            body.get("username", ""),
            body.get("password", ""),
            body.get("display_name", ""),
            email=body.get("email", ""),
            birthday=body.get("birthday"),
            invite_code=body.get("invite_code"),
            terms_accepted=terms_accepted,
            age_confirmed=age_confirmed,
            setup_token=setup_token,
            ip=ip,
            ua=ua,
        )
        return json_response(result)
    except ValueError as exc:
        _auth._record_login_fail(ip, normalized_username)
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/auth/verify-email")
async def api_verify_email(request: Request):
    """Phase 2 注册：验证 6 位码 → 创建用户行 → 颁 session cookie。"""
    body = await request.json()
    email = body.get("email", "")
    code = body.get("code", "").strip()
    if not email or not code:
        return json_response({"ok": False, "error": "email 和 code 不能为空"}, status_code=400)
    try:
        user, token = _auth.confirm_email_verification(email, code)
        workspace.ensure_default(user["id"])
        response = json_response({"ok": True, "user": public_user(user), "platform": platform_for(user)})
        _set_session_cookie(response, request, token)
        return response
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/auth/resend-code")
async def api_resend_code(request: Request):
    """重发验证码（限流 1/分钟/email）。"""
    body = await request.json()
    email = body.get("email", "")
    ip = _client_ip(request)
    if not email:
        return json_response({"ok": False, "error": "email 不能为空"}, status_code=400)
    try:
        _auth.resend_verification_code(email, ip=ip)
        return json_response({"ok": True, "message": "验证码已重发，请查收邮件"})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=429)


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


@router.post("/api/auth/login-code/request")
async def api_login_code_request(request: Request):
    """Request a one-time email code for passwordless login."""
    body = await request.json()
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    try:
        result = _auth.request_login_code(body.get("email", ""), ip=ip, ua=ua)
        return json_response(result)
    except _auth.RateLimited as rl:
        return json_response(
            {"ok": False, "error": f"请求频率过高，请 {rl.retry_after_sec} 秒后再试"},
            status_code=429,
            headers={"Retry-After": str(rl.retry_after_sec)},
        )
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/auth/login-code/verify")
async def api_login_code_verify(request: Request):
    """Verify a one-time email code and issue a session cookie."""
    body = await request.json()
    ip = _client_ip(request)
    try:
        user, token = _auth.confirm_login_code(body.get("email", ""), body.get("code", ""), ip=ip)
        workspace.ensure_default(user["id"])
        response = json_response({"ok": True, "user": public_user(user), "platform": platform_for(user)})
        _set_session_cookie(response, request, token)
        return response
    except _auth.RateLimited as rl:
        return json_response(
            {"ok": False, "error": f"请求频率过高，请 {rl.retry_after_sec} 秒后再试"},
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


@router.post("/api/auth/forgot-password")
async def api_forgot_password(request: Request):
    """触发密码重置邮件。总是返回 {'ok': True}（防枚举攻击）。"""
    body = await request.json()
    email = (body.get("email") or "").strip()
    ip = _client_ip(request)
    # 即使 email 格式错误也静默返回 ok，防止枚举
    result = _auth.request_password_reset(email, ip=ip)
    return json_response(result)


@router.post("/api/auth/reset-password")
async def api_reset_password(request: Request):
    """验证密码重置 token，设置新密码。"""
    body = await request.json()
    token = (body.get("token") or "").strip()
    new_password = body.get("password") or ""
    ip = _client_ip(request)
    if not token or not new_password:
        raise HTTPException(400, detail={"error_key": "auth.invalid_payload", "message": "参数不完整"})
    try:
        result = _auth.confirm_password_reset(token, new_password, ip=ip)
        return json_response(result)
    except ValueError as exc:
        msg = str(exc)
        if "invalid_token" in msg or "无效或已过期" in msg:
            raise HTTPException(400, detail={"error_key": "auth.reset_token_invalid_or_expired",
                                             "message": "重置链接无效或已过期，请重新申请"})
        if "已使用" in msg:
            raise HTTPException(400, detail={"error_key": "auth.reset_token_used",
                                             "message": "该重置链接已使用过"})
        raise HTTPException(400, detail={"error_key": "auth.reset_fail", "message": msg})


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
    from core.config import effective_auth_required, setup_token as configured_setup_token
    init_db()
    with connect() as db:
        user_count = db.execute("select count(*) as n from users").fetchone()["n"]
    first_user_is_admin = int(user_count) == 0
    notes: dict = {
        "min_password_length": pw_min,
        "max_password_length": 1024,
        "invite_only": False,
    }
    # P2-3: 仅本地/非鉴权模式（effective_auth_required=False）才透出 first_user_is_admin
    # server 模式下隐藏该字段，防止泄露首注册可抢 admin 的信息（CWE-200）
    if not effective_auth_required():
        notes["first_user_is_admin"] = first_user_is_admin
    # 邀请码字段：invite 模式时必填
    invite_field = {"key": "invite_code", "label": "邀请码", "type": "text", "required": notes["invite_only"]}
    register_fields = [
        {"key": "username", "label": "用户名", "type": "text", "required": True},
        {"key": "display_name", "label": "昵称(可选)", "type": "text", "required": False},
        {"key": "email", "label": "邮箱", "type": "email", "required": True, "autocomplete": "email"},
        {"key": "birthday", "label": "出生日期", "type": "date", "required": True,
         "placeholder": "YYYY-MM-DD", "note": "必须年满 18 周岁"},
        {"key": "password", "label": "密码", "type": "password", "required": True, "min_length": pw_min},
        {"key": "terms_accepted", "type": "boolean", "required": True, "label": "我已阅读并同意《服务条款》和《隐私政策》"},
        {"key": "age_confirmed", "type": "boolean", "required": True, "label": "我已年满 18 周岁"},
    ]
    if notes["invite_only"]:
        register_fields.insert(0, invite_field)
    setup_required = effective_auth_required() and first_user_is_admin and bool((configured_setup_token() or "").strip())
    if setup_required:
        register_fields.insert(0, {
            "key": "setup_token",
            "label": "Setup Token",
            "type": "password",
            "required": True,
            "autocomplete": "one-time-code",
        })
        notes["setup_token_required"] = True

    return json_response({
        "login": [
            {"key": "username", "label": "用户名或邮箱", "type": "text", "required": True},
            {"key": "password", "label": "密码", "type": "password", "required": True, "min_length": pw_min},
        ],
        "register": register_fields,
        "notes": notes,
    })
