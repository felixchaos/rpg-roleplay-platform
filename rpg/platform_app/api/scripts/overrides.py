"""platform_app.api.scripts.overrides —— 剧本 overrides + 剧本级 GM 叙事风格端点。

overrides 读/写、gm-style 读(有效值叠加)/写(merge)。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from .._deps import json_response, require_user, value_error_response
from ._shared import router

# ── script overrides API ──────────────────────────────────────────────────────

@router.get("/api/scripts/{script_id}/overrides")
async def api_get_script_overrides(script_id: int, user=Depends(require_user)):
    """查询剧本 overrides（能访问该 script 的用户均可读:owner ∪ subscriber）。"""
    with connect() as db:
        owned = db.execute(
            """SELECT 1 FROM scripts s WHERE s.id = %s AND (
                 s.owner_id = %s
                 OR s.id IN (SELECT script_id FROM user_script_subscriptions WHERE user_id = %s)
               )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    from platform_app.knowledge.script_overrides import get_overrides_by_script_id
    data = get_overrides_by_script_id(script_id)
    return json_response({"ok": True, "data": data})


@router.post("/api/scripts/{script_id}/overrides")
async def api_update_script_overrides(request: Request, script_id: int, user=Depends(require_user)):
    """更新剧本 overrides（仅 owner）。

    Body: overrides data dict（直接替换整条记录）。
    """
    with connect() as db:
        from ...perms import script_owned
        owned = script_owned(db, script_id, user["id"])
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "请求 body 必须是合法 JSON"}, status_code=400)
    # 支持两种格式: {"data": {...}} 或直接 {...}
    overrides_data = body.get("data") if isinstance(body.get("data"), dict) else body
    from platform_app.knowledge.script_overrides import upsert_overrides
    upsert_overrides(script_id, overrides_data)
    return json_response({"ok": True})


@router.get("/api/scripts/{script_id}/gm-style")
async def api_get_script_gm_style(script_id: int, user=Depends(require_user)):
    """读剧本级 GM 叙事风格。owner 或订阅者均可读(只读展示);改仍仅 owner。

    `gm_style` 返回的是【有效值】= 平台默认 → 用户个人默认 → 本剧本 override 叠加后的
    结果(与运行时 resolve_for_state 同序),而不是只读"本剧本 override"。
    修复用户反馈:设了个人默认风格的用户,打开导入剧本的风格面板却看到一排平台默认值,
    误以为"导入剧本之后叙事风格还是默认数值、没生效"——实际运行时是生效的,只是面板显示的
    是本剧本 override(空)的平台默认补全,没继承个人默认。`stored` 单独给出"本剧本真正
    override 了哪些旋钮",前端可据此区分"继承"与"本剧本专属"。"""
    with connect() as db:
        access = db.execute(
            """SELECT 1 FROM scripts s WHERE s.id = %s AND (
                 s.owner_id = %s
                 OR s.id IN (SELECT script_id FROM user_script_subscriptions WHERE user_id = %s)
               )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
    if not access:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    from agents.gm.style_config import _read_user_gm_style
    from agents.gm.style_harness import resolve_profile
    from platform_app.knowledge.script_overrides import get_overrides_by_script_id
    data = get_overrides_by_script_id(script_id) or {}
    stored = data.get("gm_style") if isinstance(data.get("gm_style"), dict) else {}
    effective = resolve_profile(
        user_default=_read_user_gm_style(user["id"]),
        script_override=stored if isinstance(stored, dict) else None,
    )
    return json_response({"ok": True, "gm_style": effective, "stored": stored})


@router.post("/api/scripts/{script_id}/gm-style")
async def api_set_script_gm_style(request: Request, script_id: int, user=Depends(require_user)):
    """写剧本级 GM 叙事风格(仅 owner)。Body: {"gm_style": {旋钮: 0-100}}。
    只 merge 进 data.gm_style,不动其它 override 字段。"""
    with connect() as db:
        from ...perms import script_owned
        owned = script_owned(db, script_id, user["id"])
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    from agents.gm.style_harness import validate_patch
    from platform_app.knowledge.script_overrides import get_overrides_by_script_id, upsert_overrides
    body = await request.json()
    try:
        clean = validate_patch(body.get("gm_style") if "gm_style" in body else body)
    except ValueError as exc:
        return value_error_response(exc)
    data = dict(get_overrides_by_script_id(script_id) or {})
    cur = dict(data.get("gm_style") if isinstance(data.get("gm_style"), dict) else {})
    cur.update(clean)
    data["gm_style"] = cur
    upsert_overrides(script_id, data)
    return json_response({"ok": True, "gm_style": cur})
