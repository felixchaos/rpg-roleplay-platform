"""platform_app.api.auth — /api/auth/* 路由。"""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request

# [Fix-3] 进程内 per-IP 回退计数器(Redis 不可用时兜底)
# 结构与 auth.py 中登录限流的 _FAIL_BUCKETS_IP/_FAIL_LOCK 同构
_VERIFY_IP_BUCKETS: dict[str, list[float]] = {}  # ip → [请求时间戳...]
_VERIFY_IP_LOCK = threading.Lock()
_VERIFY_IP_LIMIT = 60        # 与 Redis 路径阈值一致
_VERIFY_IP_WINDOW_SEC = 600  # 600s

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
    require_admin,
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
    # Cloudflare Turnstile 人机验证：仅当配置了 RPG_TURNSTILE_SECRET 时强制（fail-safe）。
    # verify 含网络 I/O（阻塞 urllib），移出 event loop 防注册风暴卡 worker。
    from .. import turnstile as _turnstile
    if _turnstile.enabled():
        _ok = await asyncio.to_thread(_turnstile.verify, body.get("turnstile_token", ""), ip=ip)
        if not _ok:
            _auth._record_login_fail(ip, normalized_username)
            return json_response(
                {"ok": False, "error": "人机验证失败，请刷新页面后重试", "error_key": "auth.captcha_failed"},
                status_code=400,
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
        # 含同步 DB + 发码邮件(SMTP),移出 event loop 防注册风暴阻塞 worker。
        result = await asyncio.to_thread(
            _auth.register,
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
        # 本地/自托管模式:register 已自动完成注册并登录(免邮箱验证)→ 设 session cookie
        # 并返回与 verify-email 同 shape,前端据此直接进入而非停在验证码页。
        if result.get("auto_verified") and result.get("session_token") and result.get("user"):
            _user = result["user"]
            try:
                workspace.ensure_default(_user["id"])
            except Exception:
                pass
            response = json_response({
                "ok": True, "auto_verified": True,
                "user": public_user(_user), "platform": platform_for(_user),
            })
            _set_session_cookie(response, request, result["session_token"])
            return response
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
    # SEC(L-5): per-IP 软上限,与 email 维度失败计数器叠加做纵深防御(防多 IP 轮询放大暴破)。
    _ip_key = _client_ip(request)
    # [round-4-P1] rate_incr 内部吞掉所有异常并返回 None(从不抛 ConnectionError/Timeout),
    #   故原 except(ConnectionError…) 永不触发 → Redis 宕机时限流完全失效。改为按返回值判断:
    #   None=Redis 不可用 → 走进程内滑动窗口兜底;有值 → 用 Redis 计数。
    import redis_bus as _rb
    _c = _rb.rate_incr(f"verifyip:{_ip_key}", 600)
    if _c is None:
        _now = time.monotonic()
        with _VERIFY_IP_LOCK:
            _bucket = _VERIFY_IP_BUCKETS.setdefault(_ip_key, [])
            _bucket.append(_now)
            _bucket[:] = [t for t in _bucket if _now - t < _VERIFY_IP_WINDOW_SEC]
            _cnt = len(_bucket)
        if _cnt > _VERIFY_IP_LIMIT:
            return json_response({"ok": False, "error": "尝试过于频繁,请稍后再试"}, status_code=429)
    elif _c > 60:
        return json_response({"ok": False, "error": "尝试过于频繁,请稍后再试"}, status_code=429)
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
        # 内部含同步 httpx.post(Resend API),移出 event loop 防阻塞 worker。
        await asyncio.to_thread(_auth.resend_verification_code, email, ip=ip)
        return json_response({"ok": True, "message": "验证码已重发，请查收邮件"})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=429)


# 保留 request：login 需要 _client_ip(request) 用于速率限制
@router.post("/api/auth/login")
async def api_login(request: Request):
    body = await request.json()
    ip = _client_ip(request)
    try:
        # Argon2id 验证 + session 创建是纯 CPU/同步阻塞;async 路由里直接跑会冻结整个
        # worker 的 event loop(含正在流式的 chat SSE)。200 人收到邀请会集中登录(登录风暴),
        # 移到线程池避免「集体卡死」。ensure_default 同样含同步 DB,一并移出。
        user, token = await asyncio.to_thread(
            _auth.login, body.get("username", ""), body.get("password", ""), ip=ip
        )
        await asyncio.to_thread(workspace.ensure_default, user["id"])
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
        # 内部含同步 httpx.post(Resend API),移出 event loop 防阻塞 worker。
        result = await asyncio.to_thread(_auth.request_login_code, body.get("email", ""), ip=ip, ua=ua)
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
        user, token = await asyncio.to_thread(
            _auth.confirm_login_code, body.get("email", ""), body.get("code", ""), ip=ip
        )
        await asyncio.to_thread(workspace.ensure_default, user["id"])
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


@router.get("/api/auth/desktop-login")
async def api_desktop_login(request: Request, token: str = "", next: str = "/Platform.html"):
    """桌面「免登录魔法链接」入口:控制台铸的一次性 token → 兑换 session cookie → 重定向进 app。
    仅本地/桌面模式可用。next 仅允许站内相对路径(防开放重定向)。"""
    from fastapi.responses import RedirectResponse

    from core.config import deployment_mode as _dm
    if (_dm() or "").strip().lower() not in {"local", "desktop", "self_hosted", "self-hosted"}:
        raise HTTPException(status_code=404, detail="仅本地部署可用")
    # 防开放重定向:next 必须是站内相对路径(单个 /,不允许 // 或 \\ 或带协议)。
    # 反斜杠要挡:浏览器会把 /\evil.com 当成协议相对 URL → 跳站外。
    dest = next if (next.startswith("/") and not next.startswith("//") and "\\" not in next and ":" not in next) else "/Platform.html"
    try:
        user, session_token = _auth.consume_desktop_login_token((token or "").strip())
        workspace.ensure_default(user["id"])
    except ValueError:
        # 失败 → 跳登录页,不泄露原因细节
        return RedirectResponse(url="/Login.html?magic=expired", status_code=302)
    response = RedirectResponse(url=dest, status_code=302)
    _set_session_cookie(response, request, session_token)
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


@router.post("/api/auth/magic-consume")
async def api_magic_consume(request: Request):
    """task: landing magic link **直接登录**(不再发 OTP — 多此一举)。

    magic_token + email 匹配本身就是双因素认证(知道邮箱 + 知道 30 天 url-safe 256-bit token)。
    再发 6 位 OTP 没增加任何安全性,反而增加摩擦。

    流程:验 token → 查/建 user(白名单 gate)→ 直接发 session cookie。
    body: {magic_token, email}
    return: {ok, user_id, username, needs_profile, session_token}  ← 跟 passwordless-verify 同 shape
    """
    body = await request.json()
    token = (body.get("magic_token") or "").strip()
    email = (body.get("email") or "").strip().lower()
    if not token or not email:
        return json_response({"ok": False, "error": "缺 magic_token 或 email"}, status_code=400)
    ip = _client_ip(request)
    # [round-4-P2] 与 login()/confirm_login_code() 对齐:先做 per-IP/per-email 限流预检,
    #   锁定中直接 429,避免每个被锁请求仍打 DB(consume_magic_token 的 allowlist SELECT)。
    try:
        _auth._check_rate_limit(ip, email)
    except _auth.RateLimited as _rl:
        return json_response({"ok": False, "error": "尝试过于频繁,请稍后再试"}, status_code=429)
    try:
        # Step 1: 校验 magic_token + email 匹配 + 30 天有效期（快速失败预筛，真正消费在 Step 2）
        await asyncio.to_thread(_auth.consume_magic_token, token, email)
        # Step 2: 直接登录(查/建 user + 原子消费 allowlist + issue session) — 跳过 OTP
        result = await asyncio.to_thread(_auth.login_via_magic_token, email, ip=ip, magic_token=token)
        # Step 3: 保证 workspace 已建好
        await asyncio.to_thread(workspace.ensure_default, result["user_id"])
        token = result.pop("session_token", "")  # SEC(M-6): 仅经 HTTPOnly cookie 下发,不写响应体
        response = json_response({"ok": True, **result})
        _set_session_cookie(response, request, token)
        return response
    except ValueError as exc:
        # [Fix-1] magic token 校验失败计入失败计数,让 per-IP/per-email 锁定生效
        _auth._record_login_fail(ip, email)
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/auth/passwordless-verify")
async def api_passwordless_verify(request: Request):
    """body: {email, code}
    若 email 已注册 → 建 session 登录；若未注册 → 必须在 allowlist 才允许建 user(无密码)，建后登录。
    return: {ok, user_id, username?, needs_profile: bool, session_token}
    """
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    code = (body.get("code") or "").strip()
    ip = _client_ip(request)
    try:
        # 含同步 DB + 可能含 httpx.post(Resend),移出 event loop 防阻塞 worker。
        result = await asyncio.to_thread(_auth.verify_passwordless_and_login, email, code, ip=ip)
        # ensure workspace exists for new/existing user
        await asyncio.to_thread(workspace.ensure_default, result["user_id"])
        token = result.pop("session_token", "")  # SEC(M-6): 仅经 HTTPOnly cookie 下发,不写响应体
        response = json_response({"ok": True, **result})
        _set_session_cookie(response, request, token)
        return response
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# admin 角色门控收敛到 _deps.require_admin(唯一来源);保留本名供 Depends(_require_admin) 旧引用。
_require_admin = require_admin


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
    # 内部含同步 httpx.post(Resend API),移出 event loop 防阻塞 worker。
    # 即使 email 格式错误也静默返回 ok，防止枚举
    result = await asyncio.to_thread(_auth.request_password_reset, email, ip=ip)
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
        # Argon2id 哈希 ~150-300ms CPU 阻塞,移出 event loop 防冻结 worker。
        result = await asyncio.to_thread(_auth.confirm_password_reset, token, new_password, ip=ip)
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
    # Cloudflare Turnstile site key（仅当配置时透出 → 前端据此渲染人机验证挂件）。
    from .. import turnstile as _turnstile
    _ts_sitekey = _turnstile.sitekey()
    if _ts_sitekey:
        notes["turnstile_sitekey"] = _ts_sitekey
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
