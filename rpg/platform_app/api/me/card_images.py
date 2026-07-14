"""platform_app.api.me.card_images —— 人设图自动同步 / 手动生成 / 历史 + 头像上传端点 (Phase 4)。

auto-image-sync 开关、generate-persona-image、persona-images 列表/set-current/url、
avatar-url / avatar 上传、persona-images upload。含 8MB 上限;MIME 魔数校验复用
_shared._detect_image_mime。纯机械搬家,行为零变化。
"""
from __future__ import annotations

import secrets

from fastapi import Depends, File, Request, UploadFile

from ...db import connect
from .._deps import json_response, require_user
from ._shared import router, _detect_image_mime

_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB


# ── 人设图自动同步 / 手动生成 / 历史 (Phase 4) ───────────────────────────

@router.post("/api/me/character-cards/{card_id}/auto-image-sync")
async def api_set_auto_image_sync(request: Request, card_id: int, user=Depends(require_user)):
    """开关人设图自动同步。Body: {enabled: bool}。"""
    from ... import user_cards
    body = await request.json()
    try:
        return json_response(user_cards.set_auto_image_sync(user["id"], card_id, bool(body.get("enabled"))))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/me/character-cards/{card_id}/generate-persona-image")
async def api_generate_persona_image(request: Request, card_id: int, user=Depends(require_user)):
    """手动触发为指定角色卡生成人设图。Body: {prompt?: str}（prompt 留空则 worker 端自动构建）。"""
    from fastapi import HTTPException as _HTTPException
    from ... import image_jobs
    from ...db import connect as _connect

    # S3: 入队前校验 card 归属（card_type in pc/persona，不存在→404）— 取全行供兜底构建提示词
    with _connect() as db:
        card_row = db.execute(
            "select * from character_cards where id = %s and user_id = %s"
            " and card_type in ('pc', 'persona')",
            (card_id, user["id"]),
        ).fetchone()
    if not card_row:
        raise _HTTPException(status_code=404, detail="角色卡不存在或无权访问")

    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        # 前端没传(「立即生成」按钮)→ 后端按角色卡【全字段】兜底构建完整提示词,不再发空串
        from ...user_cards import build_persona_prompt
        prompt = build_persona_prompt(dict(card_row))
    result = image_jobs.enqueue_image_generation(
        user["id"],
        prompt,
        "persona",
        attach={"type": "persona_image", "id": card_id, "source": "manual"},
        origin="api_direct",
    )
    # 下层返回 {error: "quota_exceeded"|"credentials_required", ...} 时透传 4xx
    if isinstance(result, dict):
        err = result.get("error", "")
        if err == "quota_exceeded":
            return json_response({"ok": False, "error": "quota_exceeded", "detail": "已达每日生图配额上限"}, status_code=429)
        if err == "credentials_required":
            return json_response({"ok": False, "error": "credentials_required", "detail": "请先在设置中配置生图服务的 API Key"}, status_code=402)
    return json_response(result)


@router.get("/api/me/character-cards/{card_id}/persona-images")
async def api_list_persona_images(card_id: int, user=Depends(require_user)):
    """列出指定角色卡的全部人设图历史（按创建时间倒序）。"""
    from ... import image_jobs
    return json_response(image_jobs.list_persona_images(user["id"], card_id))


@router.post("/api/me/character-cards/{card_id}/persona-images/{image_id}/set-current")
async def api_set_current_persona_image(card_id: int, image_id: int, user=Depends(require_user)):
    """将指定人设图设为当前图（同时更新角色卡 avatar_path）。"""
    from ... import image_jobs
    try:
        return json_response(image_jobs.set_current_persona_image(user["id"], card_id, image_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/me/character-cards/{card_id}/avatar-url")
async def api_set_card_avatar_url(request: Request, card_id: int, user=Depends(require_user)):
    """从图库 URL 设置角色卡头像（不重新上传，URL 已是合法资产）。

    鉴权：character_cards WHERE id=card_id AND user_id=user[id]。
    URL 前缀白名单：复用 _safe_avatar_path；非法 URL → 400。
    """
    from ...user_cards import _safe_avatar_path

    user_id = int(user["id"])
    body = await request.json()
    raw_url = str(body.get("url") or "").strip()
    safe_url = _safe_avatar_path(raw_url)
    if not safe_url:
        return json_response({"ok": False, "error": "不合法的图片 URL（仅允许站内资产路径）"}, status_code=400)

    with connect() as db:
        owned = db.execute(
            "select 1 from character_cards where id = %s and user_id = %s",
            (card_id, user_id),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "角色卡不存在或无权访问"}, status_code=403)

    with connect() as db:
        db.execute(
            "update character_cards set avatar_path = %s where id = %s and user_id = %s",
            (safe_url, card_id, user_id),
        )
    return json_response({"ok": True, "url": safe_url})


@router.post("/api/me/character-cards/{card_id}/persona-images/url")
async def api_set_persona_image_url(request: Request, card_id: int, user=Depends(require_user)):
    """从图库 URL 设置人设图并设为当前图（不重新上传，URL 已是合法资产）。

    鉴权：character_cards WHERE id=card_id AND user_id=user[id]。
    URL 前缀白名单：复用 _safe_avatar_path；非法 URL → 400。
    同时更新 character_cards.avatar_path。
    """
    from ...user_cards import _safe_avatar_path

    user_id = int(user["id"])
    body = await request.json()
    raw_url = str(body.get("url") or "").strip()
    safe_url = _safe_avatar_path(raw_url)
    if not safe_url:
        return json_response({"ok": False, "error": "不合法的图片 URL（仅允许站内资产路径）"}, status_code=400)

    with connect() as db:
        owned = db.execute(
            "select 1 from character_cards where id = %s and user_id = %s",
            (card_id, user_id),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "角色卡不存在或无权访问"}, status_code=403)
        db.execute(
            "update card_persona_images set is_current = false where card_id = %s",
            (card_id,),
        )
        db.execute(
            """
            insert into card_persona_images
                (card_id, image_url, source, status, is_current)
            values (%s, %s, 'manual', 'done', true)
            """,
            (card_id, safe_url),
        )
        db.execute(
            "update character_cards set avatar_path = %s where id = %s and user_id = %s",
            (safe_url, card_id, user_id),
        )
    return json_response({"ok": True, "url": safe_url})


@router.post("/api/me/character-cards/{card_id}/avatar")
async def api_upload_card_avatar(card_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """手动上传角色卡头像（替换 avatar_path）。

    鉴权：character_cards WHERE id=card_id AND user_id=user[id]。
    MIME 魔数白名单：PNG / JPEG / WebP。大小上限 8 MB。
    """
    user_id = int(user["id"])

    # 1. ownership 校验
    with connect() as db:
        owned = db.execute(
            "select 1 from character_cards where id = %s and user_id = %s",
            (card_id, user_id),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "角色卡不存在或无权访问"}, status_code=403)

    # 2. 读取文件
    data = await file.read()
    if len(data) > _MAX_IMAGE_BYTES:
        return json_response({"ok": False, "error": f"文件过大（上限 {_MAX_IMAGE_BYTES // 1024 // 1024} MB）"}, status_code=400)

    # 3. MIME 魔数校验
    try:
        mime, ext = _detect_image_mime(data)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    # 4. 存储
    from ... import storage as _storage
    token = secrets.token_hex(12)
    filename = f"upload_{user_id}_{token}.{ext}"
    storage_key, url = _storage.store_bytes(data, kind="ai_images", filename=filename)

    # 5. 更新 character_cards.avatar_path
    with connect() as db:
        db.execute(
            "update character_cards set avatar_path = %s where id = %s and user_id = %s",
            (url, card_id, user_id),
        )

    # 6. 登记资产
    from ... import assets_registry as _reg
    _reg.register_asset(
        user_id=user_id,
        kind="card_image",
        storage_key=storage_key,
        url=url,
        source="manual_upload",
        ref_kind="card",
        ref_id=card_id,
        mime=mime,
        size=len(data),
    )

    return json_response({"ok": True, "url": url})


@router.post("/api/me/character-cards/{card_id}/persona-images/upload")
async def api_upload_persona_image(card_id: int, file: UploadFile = File(...), user=Depends(require_user)):
    """手动上传人设图，插入 card_persona_images 并设为当前图（翻 is_current）。

    鉴权：character_cards WHERE id=card_id AND user_id=user[id]。
    MIME 魔数白名单：PNG / JPEG / WebP。大小上限 8 MB。
    """
    user_id = int(user["id"])

    # 1. ownership 校验
    with connect() as db:
        owned = db.execute(
            "select 1 from character_cards where id = %s and user_id = %s",
            (card_id, user_id),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "角色卡不存在或无权访问"}, status_code=403)

    # 2. 读取文件
    data = await file.read()
    if len(data) > _MAX_IMAGE_BYTES:
        return json_response({"ok": False, "error": f"文件过大（上限 {_MAX_IMAGE_BYTES // 1024 // 1024} MB）"}, status_code=400)

    # 3. MIME 魔数校验
    try:
        mime, ext = _detect_image_mime(data)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    # 4. 存储
    from ... import storage as _storage
    token = secrets.token_hex(12)
    filename = f"upload_{user_id}_{token}.{ext}"
    storage_key, url = _storage.store_bytes(data, kind="ai_images", filename=filename)

    # 5. 落库 card_persona_images：翻 is_current + 插新行 + 更新 avatar_path
    with connect() as db:
        # 先把该卡已有 is_current 行清掉
        db.execute(
            "update card_persona_images set is_current = false where card_id = %s",
            (card_id,),
        )
        # 插入新行
        db.execute(
            """
            insert into card_persona_images
                (card_id, image_url, source, status, is_current)
            values (%s, %s, 'manual', 'done', true)
            """,
            (card_id, url),
        )
        # 同步更新角色卡头像
        db.execute(
            "update character_cards set avatar_path = %s where id = %s and user_id = %s",
            (url, card_id, user_id),
        )

    # 6. 登记资产
    from ... import assets_registry as _reg
    _reg.register_asset(
        user_id=user_id,
        kind="card_image",
        storage_key=storage_key,
        url=url,
        source="manual_upload",
        ref_kind="card",
        ref_id=card_id,
        mime=mime,
        size=len(data),
    )

    return json_response({"ok": True, "url": url})
