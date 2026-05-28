"""platform_app.api.platform — /api/platform, /api/platform/commands, /api/profile 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import auth as _auth
from ..security import public_user
from ._deps import (
    _auth_required,
    command_payload,
    current_user,
    json_response,
    platform_for,
    require_user,
)

router = APIRouter()


@router.get("/api/platform")
async def api_platform(request: Request):
    user = current_user(request)
    # 服务器/生产模式下未登录拒绝返回任何平台信息
    if not user and _auth_required():
        return json_response({"ok": False, "error": "需要登录"}, status_code=401)
    return json_response(platform_for(user))


@router.post("/api/profile")
async def api_profile(request: Request):
    user = require_user(request)
    body = await request.json()
    updated = _auth.update_profile(user["id"], body.get("display_name", user["display_name"]), body.get("bio", ""))
    return json_response({"ok": True, "user": public_user(updated)})


@router.get("/api/platform/commands")
async def api_commands(request: Request):
    """命令清单：未登录 + 服务器模式下拒绝；登录用户可见，但隐藏 admin-only 命令"""
    user = current_user(request)
    if not user and _auth_required():
        return json_response({"ok": False, "error": "需要登录"}, status_code=401)
    return json_response({"ok": True, "commands": command_payload()})
