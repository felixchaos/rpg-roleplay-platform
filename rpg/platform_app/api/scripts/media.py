"""platform_app.api.scripts.media —— 剧本封面 + NPC 角色卡头像端点。

cover-url / cover 上传、NPC 卡 avatar-url / avatar 上传。含封面 MIME 魔数校验与
严格 owner 闸辅助。纯机械搬家,行为零变化。
"""
from __future__ import annotations

import secrets

from fastapi import Depends, File, Request, UploadFile

from ...db import connect
from ...perms import script_owned
from .._deps import json_response, require_user, value_error_response

# 封面 MIME 魔数校验与 me._shared._detect_image_mime 逐字节相同(PNG/JPEG/WebP 同判、同
# ValueError 文案)—— 收敛到单一真相源,本地保留 _detect_cover_mime 名(调用点零改动)。
from ..me._shared import _detect_image_mime as _detect_cover_mime
from ._shared import router

_MAX_COVER_BYTES = 8 * 1024 * 1024  # 8 MB


@router.post("/api/scripts/{script_id}/cover-url")
async def api_set_script_cover_url(request: Request, script_id: int, user=Depends(require_user)):
    """从图库 URL 设置剧本封面（不重新上传，URL 已是合法资产）。

    鉴权：scripts WHERE id=script_id AND owner_id=user[id]（仅 owner）。
    URL 前缀白名单：复用 _safe_avatar_path；非法 URL → 400。
    """
    from platform_app.user_cards import _safe_avatar_path

    user_id = int(user["id"])
    body = await request.json()
    raw_url = str(body.get("url") or "").strip()
    safe_url = _safe_avatar_path(raw_url)
    if not safe_url:
        return json_response({"ok": False, "error": "不合法的图片 URL（仅允许站内资产路径）"}, status_code=400)

    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权操作该剧本"}, status_code=403)
        db.execute(
            "update scripts set cover_image_url = %s where id = %s and owner_id = %s",
            (safe_url, script_id, user_id),
        )
    return json_response({"ok": True, "url": safe_url})


# ── NPC 角色卡头像（剧本所有者管；NPC 卡 user_id=NULL，挂 script_id，故 owner 走 scripts.owner_id）──

def _require_script_owner(db, script_id: int, user_id: int) -> bool:
    # 严格 owner SQL 收敛到 perms.script_owned(唯一来源,签名统一 db,script_id,user_id)。
    return bool(script_owned(db, script_id, user_id))


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/avatar-url")
async def api_set_npc_card_avatar_url(request: Request, script_id: int, card_id: int, user=Depends(require_user)):
    """从图库 URL 设置 NPC 角色卡头像。鉴权：scripts.owner_id；卡必须属于该剧本。"""
    from platform_app.user_cards import _safe_avatar_path
    user_id = int(user["id"])
    body = await request.json()
    safe_url = _safe_avatar_path(str(body.get("url") or "").strip())
    if not safe_url:
        return json_response({"ok": False, "error": "不合法的图片 URL（仅允许站内资产路径）"}, status_code=400)
    with connect() as db:
        if not _require_script_owner(db, script_id, user_id):
            return json_response({"ok": False, "error": "无权操作该剧本"}, status_code=403)
        res = db.execute(
            "update character_cards set avatar_path = %s where id = %s and script_id = %s",
            (safe_url, card_id, script_id),
        )
    if getattr(res, "rowcount", 0) == 0:
        return json_response({"ok": False, "error": "角色卡不属于该剧本"}, status_code=404)
    return json_response({"ok": True, "url": safe_url})


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/avatar")
async def api_upload_npc_card_avatar(script_id: int, card_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """上传 NPC 角色卡头像。鉴权：scripts.owner_id；卡必须属于该剧本。PNG/JPEG/WebP ≤8MB。"""
    user_id = int(user["id"])
    with connect() as db:
        if not _require_script_owner(db, script_id, user_id):
            return json_response({"ok": False, "error": "无权操作该剧本"}, status_code=403)
    data = await file.read()
    if len(data) > _MAX_COVER_BYTES:
        return json_response({"ok": False, "error": f"文件过大（上限 {_MAX_COVER_BYTES // 1024 // 1024} MB）"}, status_code=400)
    try:
        mime, ext = _detect_cover_mime(data)
    except ValueError as exc:
        return value_error_response(exc)
    from ... import storage as _storage
    filename = f"upload_{user_id}_{secrets.token_hex(12)}.{ext}"
    storage_key, url = _storage.store_bytes(data, kind="ai_images", filename=filename)
    with connect() as db:
        res = db.execute(
            "update character_cards set avatar_path = %s where id = %s and script_id = %s",
            (url, card_id, script_id),
        )
    if getattr(res, "rowcount", 0) == 0:
        return json_response({"ok": False, "error": "角色卡不属于该剧本"}, status_code=404)
    try:
        from ... import assets_registry as _reg
        _reg.register_asset(user_id=user_id, kind="card_image", storage_key=storage_key, url=url,
                            source="manual_upload", ref_kind="card", ref_id=int(card_id), mime=mime, size=len(data))
    except Exception:
        pass
    return json_response({"ok": True, "url": url})


@router.post("/api/scripts/{script_id}/cover")
async def api_upload_script_cover(script_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """手动上传剧本封面图（替换 cover_image_url）。

    鉴权：scripts WHERE id=script_id AND owner_id=user[id]（仅 owner）。
    MIME 魔数白名单：PNG / JPEG / WebP。大小上限 8 MB。
    """
    user_id = int(user["id"])

    # 1. ownership 校验（只有 owner 能改封面，订阅者不行）
    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权操作该剧本"}, status_code=403)

    # 2. 读取文件
    data = await file.read()
    if len(data) > _MAX_COVER_BYTES:
        return json_response(
            {"ok": False, "error": f"文件过大（上限 {_MAX_COVER_BYTES // 1024 // 1024} MB）"},
            status_code=400,
        )

    # 3. MIME 魔数校验
    try:
        mime, ext = _detect_cover_mime(data)
    except ValueError as exc:
        return value_error_response(exc)

    # 4. 存储
    from ... import storage as _storage
    token = secrets.token_hex(12)
    filename = f"upload_{user_id}_{token}.{ext}"
    storage_key, url = _storage.store_bytes(data, kind="ai_images", filename=filename)

    # 5. 更新 scripts.cover_image_url
    with connect() as db:
        db.execute(
            "update scripts set cover_image_url = %s where id = %s and owner_id = %s",
            (url, script_id, user_id),
        )

    # 6. 登记资产
    from ... import assets_registry as _reg
    _reg.register_asset(
        user_id=user_id,
        kind="cover",
        storage_key=storage_key,
        url=url,
        source="manual_upload",
        ref_kind="script",
        ref_id=script_id,
        mime=mime,
        size=len(data),
    )

    return json_response({"ok": True, "url": url})
