"""platform_app.api.me.preferences —— 界面偏好 + 用户级 GM 叙事风格端点。

preference 合并/覆盖、gm-style schema、gm-style 读(补全)/写(merge)。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request
from psycopg.types.json import Jsonb

from ...db import connect
from .._deps import json_response, require_user
from ._shared import router


@router.post("/api/me/preference")
async def api_set_preference(request: Request, user=Depends(require_user)):
    """更新或合并界面偏好（主题/字号/默认模型...）"""
    body = await request.json()
    # 支持两种写法：整对象覆盖 (replace=true) 或 patch 合并 (默认)
    replace = bool(body.get("replace", False))
    payload = body.get("preferences") if "preferences" in body else body.get("value", body)
    if not isinstance(payload, dict):
        return json_response({"ok": False, "error": "preferences 必须是对象"}, status_code=400)
    # SEC(H-13): payload 字节上限,防认证用户反复 POST 大 JSON 做存储放大 DoS(JSONB || 合并无界增长)。
    import json as _json
    if len(_json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 32 * 1024:
        return json_response({"ok": False, "error": "preferences 过大(上限 32KB)"}, status_code=400)
    with connect() as db:
        if replace:
            row = db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set preferences = excluded.preferences, updated_at = now()
                returning preferences, updated_at
                """,
                (user["id"], Jsonb(payload)),
            ).fetchone()
        else:
            row = db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set
                  preferences = user_preferences.preferences || excluded.preferences,
                  updated_at = now()
                returning preferences, updated_at
                """,
                (user["id"], Jsonb(payload)),
            ).fetchone()
    return json_response({"ok": True, "preferences": dict(row["preferences"]), "updated_at": str(row["updated_at"])})


@router.get("/api/gm-style/schema")
async def api_gm_style_schema(user=Depends(require_user)):
    """返回 GM 叙事风格 6 旋钮的 key + 默认值,供前端滑块与后端保持同步。"""
    from agents.gm.style_harness import KNOBS, default_profile
    return json_response({"ok": True, "knobs": list(KNOBS.keys()), "defaults": default_profile()})


@router.get("/api/me/gm-style")
async def api_get_my_gm_style(user=Depends(require_user)):
    """读当前用户级 GM 风格默认(用 schema 默认补全未设的旋钮)。"""
    from agents.gm.style_harness import normalize_profile
    with connect() as db:
        row = db.execute(
            "select preferences from user_preferences where user_id = %s", (user["id"],)
        ).fetchone()
    prefs = (row and dict(row["preferences"])) or {}
    stored = prefs.get("gm_style") if isinstance(prefs.get("gm_style"), dict) else {}
    return json_response({"ok": True, "gm_style": normalize_profile(stored), "stored": stored})


@router.post("/api/me/gm-style")
async def api_set_my_gm_style(request: Request, user=Depends(require_user)):
    """写用户级 GM 风格默认。Body: {"gm_style": {旋钮: 0-100}}。只校验已知 6 键。"""
    from agents.gm.style_harness import validate_patch
    body = await request.json()
    try:
        clean = validate_patch(body.get("gm_style") if "gm_style" in body else body)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    # patch 合并进 preferences.gm_style(保留其它偏好 + 已设旋钮)
    with connect() as db:
        row = db.execute(
            """
            insert into user_preferences(user_id, preferences)
            values (%s, %s)
            on conflict(user_id) do update set
              preferences = jsonb_set(
                coalesce(user_preferences.preferences, '{}'::jsonb), '{gm_style}',
                coalesce(user_preferences.preferences->'gm_style', '{}'::jsonb) || %s, true),
              updated_at = now()
            returning preferences
            """,
            (user["id"], Jsonb({"gm_style": clean}), Jsonb(clean)),
        ).fetchone()
    saved = dict(row["preferences"]).get("gm_style", {}) if row else clean
    return json_response({"ok": True, "gm_style": saved})
