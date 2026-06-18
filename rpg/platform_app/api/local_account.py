"""platform_app.api.local_account — 桌面/本地部署的「默认账户」管理 + 免登录魔法链接。

仅在本地/自部署模式启用(服务器模式 404)。改账户名/密码、铸一次性魔法链接的写操作
额外要求请求来自本机回环(127.0.0.1)—— 只有跑在这台机器上的控制台能改,LAN 设备改不了。
账户 id 始终不变 → 用户改用户名/密码后仍登录回同一账户、数据不丢。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import auth as _auth
from ..security import public_user
from ._deps import _is_loopback, _set_session_cookie, current_user, json_response

router = APIRouter()

_LOCAL_MODES = {"local", "desktop", "self_hosted", "self-hosted"}


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
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
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
