"""platform_app.frontend_routes.profile —— PROFILE 补充路由(/api/profile/* + /api/me/preference)。

原单文件「PROFILE supplements」段逐端点搬运,零行为变化:
头像上传(魔数校验)/ 从图库设头像 / 重置头像 / 头像取图 / 可见性 / me 偏好合并。
_ensure_profile_extras_table 为空占位函数(v33 migration 已建表),被 api/platform.py 与
api/me/profile.py import,故随本段保留并经 __init__ 门面 re-export 不变。
"""
from __future__ import annotations

import os
import time

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from ..api import json_response, require_user
from ..db import connect, init_db

# 头像落盘统一走 storage 模块（S1 基座）
from ..storage import resolve_path as _storage_resolve_path
from ..storage import store_bytes as _storage_store_bytes
from ._shared import _bad, router


# ------------------------------------------------------------
#  PROFILE supplements
# ------------------------------------------------------------
@router.post("/api/profile/avatar")
async def api_upload_avatar(request: Request):
    user = require_user(request)
    form = await request.form()
    f = form.get("file")
    if not f or not getattr(f, "filename", ""):
        return _bad("请选择文件")
    ext = os.path.splitext(f.filename or "")[-1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        return _bad("仅支持 PNG / JPG / WEBP")
    data = await f.read()
    if len(data) > 2 * 1024 * 1024:
        return _bad("文件超过 2 MB")
    # 魔数校验:不信客户端文件名扩展名,以真实图片字节为准(与角色卡/人设图/卡导入
    # 三处上传一致,见 api/me.py:_detect_image_mime)。伪造扩展名的非图片 → 400,
    # 落盘扩展名取检测结果(防止任意字节伪装成 .png 进头像图床并以 image/* 回发)。
    from ..api.me import _detect_image_mime
    try:
        _mime, _detected_ext = _detect_image_mime(data)
    except ValueError as exc:
        return _bad(str(exc))
    safe_name = f"u{user['id']}_{int(time.time())}.{_detected_ext}"
    # 落盘走 storage.store_bytes（统一根 AVATARS_DIR）
    _storage_key, _new_url = _storage_store_bytes(data, kind="avatars", filename=safe_name)
    # 对外 URL 仍用旧路径保持向后兼容，前端老 URL 不破
    avatar_url = f"/api/profile/avatar/file/{safe_name}"
    init_db()
    with connect() as db:
        db.execute(
            "update users set avatar_url = %s, updated_at = now() where id = %s",
            (avatar_url, user["id"]),
        )
    # 登记 user_assets（失败只 log，不影响上传主流程）
    try:
        from platform_app.assets_registry import register_asset  # lazy import
        register_asset(
            user_id=int(user["id"]),
            kind="avatar",
            storage_key=f"avatars/{safe_name}",
            url=avatar_url,
            source="avatar_upload",
            ref_kind="user",
            ref_id=int(user["id"]),
            mime=_mime,
            size=len(data),
        )
    except Exception as _reg_exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[frontend_routes] register_asset(avatar) failed user=%s: %s",
            user["id"], _reg_exc,
        )
    return json_response({"ok": True, "avatar_url": avatar_url})


@router.post("/api/profile/avatar-url")
async def api_set_avatar_url(request: Request):
    """从图库 URL 设置个人头像（不重新上传，URL 已是合法资产）。

    URL 前缀白名单：复用 _safe_avatar_path；非法 URL → 400。
    """
    from platform_app.user_cards import _safe_avatar_path

    user = require_user(request)
    body = await request.json()
    raw_url = str(body.get("url") or "").strip()
    safe_url = _safe_avatar_path(raw_url)
    if not safe_url:
        return json_response({"ok": False, "error": "不合法的图片 URL（仅允许站内资产路径）"}, status_code=400)

    init_db()
    with connect() as db:
        db.execute(
            "update users set avatar_url = %s, updated_at = now() where id = %s",
            (safe_url, user["id"]),
        )
    return json_response({"ok": True, "url": safe_url})


@router.post("/api/profile/avatar/reset")
async def api_reset_avatar(request: Request):
    user = require_user(request)
    init_db()
    with connect() as db:
        db.execute("update users set avatar_url = null, updated_at = now() where id = %s", (user["id"],))
    return json_response({"ok": True})


@router.get("/api/profile/avatar/file/{name}")
async def api_avatar_file(name: str, request: Request):
    # 鉴权链接:必须登录(cookie)才能取图,未登录 → 401(同 /api/storage、/api/images/file)。
    require_user(request)
    # Basic safety: only allow our generated naming pattern.
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404)
    # 服务改走 storage.resolve_path（统一根 AVATARS_DIR），老 URL 保持兼容
    try:
        path = _storage_resolve_path(f"avatars/{name}")
    except ValueError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path))


def _ensure_profile_extras_table() -> None:
    # v33 migration 已建表，懒创建废弃。保留空函数避免 me.py / platform.py 的导入报错。
    pass


@router.post("/api/profile/visibility")
async def api_profile_visibility(request: Request):
    user = require_user(request)
    body = await request.json() or {}
    from psycopg.types.json import Jsonb
    with connect() as db:
        db.execute(
            """
            insert into profile_extras(user_id, visibility)
            values (%s, %s)
            on conflict (user_id) do update set visibility = excluded.visibility, updated_at = now()
            """,
            (user["id"], Jsonb(body)),
        )
    return json_response({"ok": True, "visibility": body})


# Persist any additional “me/preference” keys here too.
@router.post("/api/me/preference")
async def api_save_preference(request: Request):
    user = require_user(request)
    body = await request.json() or {}
    from psycopg.types.json import Jsonb
    # Merge over existing preferences
    with connect() as db:
        row = db.execute(
            "select preferences from profile_extras where user_id = %s",
            (user["id"],),
        ).fetchone()
        prefs = (row and row["preferences"]) or {}
        prefs.update(body)
        db.execute(
            """
            insert into profile_extras(user_id, preferences)
            values (%s, %s)
            on conflict (user_id) do update set preferences = excluded.preferences, updated_at = now()
            """,
            (user["id"], Jsonb(prefs)),
        )
    return json_response({"ok": True, "preferences": prefs})


# 注:GET /api/me/profile 已统一到 platform_app/api/me.py(返回 user+profile+extras+
# 偏好+用量+凭证,超集)。此处原有的重复路由会被 platform_router(先挂载)遮蔽,
# 已删除以消除路由冲突。
