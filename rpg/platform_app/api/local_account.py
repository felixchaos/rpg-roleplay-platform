"""platform_app.api.local_account — 桌面/本地部署的「默认账户」管理 + 免登录魔法链接。

仅在本地/自部署模式启用(服务器模式 404)。改账户名/密码、铸一次性魔法链接的写操作
额外要求请求来自本机回环(127.0.0.1)—— 只有跑在这台机器上的控制台能改,LAN 设备改不了。
账户 id 始终不变 → 用户改用户名/密码后仍登录回同一账户、数据不丢。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import auth as _auth
from ..security import public_user
from ._deps import (
    _is_loopback,
    _set_session_cookie,
    json_response,
    value_error_response,
)

router = APIRouter()

from core.config import LOCAL_MODES as _LOCAL_MODES


def _local_mode() -> bool:
    from core.config import deployment_mode as _dm

    return (_dm() or "").strip().lower() in _LOCAL_MODES


def _require_local(request: Request, *, loopback: bool = False) -> None:
    """本地模式 gate。loopback=True 时还要求请求来自本机(写操作)。"""
    if not _local_mode():
        raise HTTPException(status_code=404, detail="本接口仅本地部署可用")
    if loopback and not _is_loopback(request):
        raise HTTPException(status_code=403, detail="账户设置只能在本机控制台修改")


def _account_view(acct: dict | None) -> dict:
    if not acct:
        return {"exists": False}
    return {
        "exists": True,
        "id": acct.get("id"),
        "username": acct.get("username"),
        "display_name": acct.get("display_name") or acct.get("username"),
        "avatar_path": acct.get("avatar_path"),
        "has_password": bool(acct.get("password_hash")),
    }


@router.get("/api/local/account")
async def get_local_account(request: Request):
    """读本地默认账户信息(用户名/昵称/头像/是否设密码)。"""
    _require_local(request)
    acct = _auth.bootstrap_local_account()  # 幂等:确保存在
    return json_response({"ok": True, "account": _account_view(acct)})


@router.post("/api/local/account/profile")
async def update_local_profile(request: Request):
    """改本地账户用户名 / 昵称(本机回环)。id 不变。"""
    _require_local(request, loopback=True)
    body = await request.json()
    acct = _auth.bootstrap_local_account()
    try:
        updated = _auth.update_local_account(
            int(acct["id"]),
            username=body.get("username") if "username" in body else None,
            display_name=body.get("display_name") if "display_name" in body else None,
        )
    except ValueError as exc:
        return value_error_response(exc)
    return json_response({"ok": True, "account": _account_view(updated)})


@router.post("/api/local/account/password")
async def set_local_password(request: Request):
    """设/改/清除本地账户密码(本机回环)。空 = 清除 → 回到回环免登录。"""
    _require_local(request, loopback=True)
    body = await request.json()
    pw = (body.get("password") or "")
    if pw and len(pw) > 1024:
        return json_response({"ok": False, "error": "密码过长"}, status_code=400)
    acct = _auth.bootstrap_local_account()
    _auth.set_account_password(int(acct["id"]), pw)
    return json_response({"ok": True, "has_password": bool(pw)})


@router.post("/api/local/account/magic-token")
async def mint_magic_token(request: Request):
    """铸一次性「免登录魔法链接」token(本机回环,控制台主进程调用)。
    返回 token + 相对路径;浏览器打开 /api/auth/desktop-login?token= 即登录。"""
    _require_local(request, loopback=True)
    acct = _auth.bootstrap_local_account()
    token = _auth.create_desktop_login_token(int(acct["id"]))
    return json_response({"ok": True, "token": token,
                          "path": f"/api/auth/desktop-login?token={token}"})


# ── 邀请链接:控制台(回环)铸/撤销可复用邀请 token;局域网内的人凭它轻量注册自己的账号 ──
@router.post("/api/local/account/invite-token")
async def mint_invite_token(request: Request):
    """铸一枚可复用邀请 token(本机回环)。浏览器打开 /Login.html?invite= 走轻量注册。"""
    _require_local(request, loopback=True)
    acct = _auth.bootstrap_local_account()
    token = _auth.create_desktop_invite_token(int(acct["id"]))
    return json_response({"ok": True, "token": token,
                          "path": f"/Login.html?invite={token}"})


@router.post("/api/local/account/invite-token/revoke")
async def revoke_invite_tokens(request: Request):
    """撤销全部邀请 token(停止邀请;本机回环)。"""
    _require_local(request, loopback=True)
    _auth.revoke_desktop_invite_tokens()
    return json_response({"ok": True})


@router.post("/api/local/register")
async def register_via_invite(request: Request):
    """局域网设备凭邀请 token 轻量注册(用户名+密码,无邮箱)→ 注册即登录(set cookie)。
    本地/自部署模式可用;**不要求回环**(就是给 LAN 设备用的)。token 无效/弱密码/重名 → 400。"""
    _require_local(request)  # 仅本地部署;LAN 设备(非回环)可访问
    body = await request.json()
    try:
        user, session_token = _auth.register_via_invite(
            (body.get("invite") or "").strip(),
            (body.get("username") or "").strip(),
            body.get("password") or "",
            display_name=(body.get("display_name") or "").strip(),
            age_confirmed=bool(body.get("age_confirmed")),
        )
    except ValueError as exc:
        return value_error_response(exc)
    resp = json_response({"ok": True, "user": public_user(user), "next": "/Platform.html"})
    _set_session_cookie(resp, request, session_token)
    return resp
