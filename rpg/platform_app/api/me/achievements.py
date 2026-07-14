"""platform_app.api.me.achievements —— 成就目录 / 用户态评估 / 公开成就墙端点。

见 docs/design/I_achievements.md。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends

from ...db import connect
from .._deps import json_response, require_user
from ._shared import router


# ── 成就(见 docs/design/I_achievements.md) ──────────────────────────
@router.get("/api/achievements")
async def api_public_achievements():
    """公开目录:全锁态、隐藏成就打码。匿名预览用此(替代前端 mock)。"""
    from ...achievements import public_catalog
    with connect() as db:
        items = public_catalog(db)
    return json_response({"ok": True, "items": items})


@router.get("/api/me/achievements")
async def api_my_achievements(user=Depends(require_user)):
    """用户态:懒评估 + 落新解锁,返回完整列表 + newly_unlocked(给前端弹 toast)。"""
    from ...achievements import evaluate
    with connect() as db:
        result = evaluate(db, user)
    return json_response({"ok": True, **result})


@router.post("/api/me/achievements/seen")
async def api_my_achievements_seen(user=Depends(require_user)):
    """标记全部 unseen→seen(看过解锁提示后调)。"""
    with connect() as db:
        db.execute(
            "update user_achievements set seen = true where user_id = %s and seen = false",
            (user["id"],),
        )
    return json_response({"ok": True})


@router.get("/api/u/{username}/achievements")
async def api_public_wall(username: str, viewer=Depends(require_user)):
    """Phase 3:某用户的公开成就墙。受其「公开个人主页」开关(user_preferences.public_profile)约束;
    未开启或用户不存在一律 404(不泄露存在性)。需登录查看(server 模式整站已鉴权)。"""
    from ...achievements import public_wall
    with connect() as db:
        u = db.execute(
            "select id, username, display_name from users where lower(username) = lower(%s)",
            (username,),
        ).fetchone()
        if not u:
            return json_response({"ok": False, "error": "not found"}, status_code=404)
        pref_row = db.execute(
            "select preferences->>'public_profile' as pp from user_preferences where user_id = %s",
            (u["id"],),
        ).fetchone()
        is_public = bool(pref_row and pref_row["pp"] == "true")
        is_self = bool(viewer and viewer.get("id") == u["id"])
        if not (is_public or is_self):
            return json_response({"ok": False, "error": "not found"}, status_code=404)
        wall = public_wall(db, {"id": u["id"]})
    return json_response({
        "ok": True,
        "username": u["username"],
        "display_name": u["display_name"],
        "is_self": is_self,
        "public": is_public,
        **wall,
    })
