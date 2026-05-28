"""platform_app.api.settings — /api/settings GET/POST 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import settings as _settings
from ._deps import json_response, require_user

router = APIRouter()


@router.get("/api/settings")
async def api_settings(user=Depends(require_user)):
    return json_response({"ok": True, "settings": _settings.list_settings(user["id"])})


@router.post("/api/settings")
async def api_save_setting(request: Request, user=Depends(require_user)):
    body = await request.json()
    return json_response({"ok": True, "settings": _settings.set_setting(user["id"], body.get("key", ""), body.get("value"))})
