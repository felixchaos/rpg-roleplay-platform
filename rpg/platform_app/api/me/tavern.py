"""platform_app.api.me.tavern —— 酒馆(SillyTavern)角色卡兼容 + 聊天记录导入端点。

角色卡 import-tavern / import-json、export-tavern / export-png、聊天记录 import-tavern(JSONL)。
纯机械搬家,行为零变化。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import Depends, Request

from .._deps import json_response, require_user
from ._shared import router, _store_imported_card_image


# ── 酒馆 (SillyTavern) 角色卡兼容 ───────────────────────────────────
def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@router.post("/api/me/character-cards/import-tavern")
async def api_import_tavern_card(request: Request, user=Depends(require_user)):
    """导入酒馆角色卡。

    两种 Content-Type 均支持：
    A) multipart/form-data: 含 "file" 字段（.png/.json/.webp 文件）
    B) application/json payload 形态:
      - {"json": {...V2 dict...}}
      - {"json_string": "{...}"}
      - {"base64": "..."}
      - {"png_base64": "..."}
    """
    from ... import tavern_cards, user_cards
    _MAX_IMPORT_PAYLOAD_BYTES = 16 * 1024 * 1024

    content_type = request.headers.get("content-type", "")
    ai_split = False  # 用户显式 opt-in「AI 整理字段」时才挂 LLM 兜底
    image_bytes: bytes | None = None  # PNG/WEBP 卡的原图，导入后存为头像 + 登记文件库
    # 整理用模型统一走「设置 → 模型 → card_import」配置(apply_llm_structure 内部解析),
    # 不在导入请求里透传 per-import 模型。
    try:
        # ── multipart/form-data（前端 importTavern(file)）─────────────
        if "multipart/form-data" in content_type:
            form = await request.form()
            ai_split = _truthy(form.get("ai_split"))
            file_field = form.get("file")
            if file_field is None:
                return json_response({"ok": False, "error": "multipart 中缺少 file 字段"}, status_code=400)
            blob = await file_field.read()
            if len(blob) > _MAX_IMPORT_PAYLOAD_BYTES:
                raise ValueError(f"文件过大（上限 {_MAX_IMPORT_PAYLOAD_BYTES // (1024*1024)} MB）")
            fname = getattr(file_field, "filename", "") or ""
            if fname.lower().endswith(".png") or fname.lower().endswith(".webp"):
                v2 = tavern_cards.parse_png_card(blob)
                image_bytes = blob  # PNG/WEBP 卡本身即头像图
            else:
                # treat as JSON
                try:
                    v2 = tavern_cards.parse_card(blob.decode("utf-8", errors="replace"))
                except Exception as exc:
                    raise ValueError(f"JSON 解析失败：{exc}") from exc
        # ── JSON body ────────────────────────────────────────────────
        else:
            body = await request.json()
            ai_split = _truthy(body.get("ai_split"))
            if body.get("png_base64"):
                import base64 as _b64
                png_b64 = body["png_base64"]
                if not isinstance(png_b64, str) or len(png_b64) > _MAX_IMPORT_PAYLOAD_BYTES:
                    raise ValueError(f"png_base64 过大或非字符串（上限 {_MAX_IMPORT_PAYLOAD_BYTES} 字节）")
                try:
                    blob = _b64.b64decode(png_b64, validate=True)
                except Exception as exc:
                    raise ValueError(f"png_base64 不合法：{exc}") from exc
                if len(blob) > 10 * 1024 * 1024:
                    raise ValueError("PNG 文件过大（解码后最大 10MB）")
                v2 = tavern_cards.parse_png_card(blob)
                image_bytes = blob  # PNG 卡本身即头像图
            elif body.get("json") is not None:
                v2 = tavern_cards.parse_card(body["json"])
            elif body.get("json_string"):
                v2 = tavern_cards.parse_card(body["json_string"])
            elif body.get("base64"):
                v2 = tavern_cards.parse_card(body["base64"])
            else:
                return json_response({"ok": False, "error": "需要 file(multipart) / json / json_string / base64 / png_base64 之一"}, status_code=400)

        payload = tavern_cards.tavern_to_user_card(v2)
        if ai_split:
            # LLM 兜底拆分(同步调用包进线程,失败不阻断导入)。模型走 card_import 统一配置,usage 自动入账。
            try:
                payload, _used = await asyncio.to_thread(
                    tavern_cards.apply_llm_structure, payload, user["id"]
                )
            except Exception:
                pass
        card = user_cards.upsert_user_card(user["id"], payload)
        # 导入的角色卡若带原图(PNG/WEBP 卡)→ 存为头像 + 登记进文件库(功能组件→资产)。失败不阻断导入。
        if image_bytes and isinstance(card, dict) and card.get("id"):
            try:
                _store_imported_card_image(user["id"], int(card["id"]), image_bytes)
                card = user_cards.get_user_card(user["id"], int(card["id"])) or card
            except Exception as _img_exc:
                import logging as _logging
                _logging.getLogger(__name__).warning("[import-tavern] store card image failed: %s", _img_exc)
        return json_response({
            "ok": True, "card": card, "imported_from": "tavern_v2",
            "llm_structured": bool((payload.get("metadata") or {}).get("llm_structured_description")),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}/export-tavern")
async def api_export_tavern_card(card_id: int, user=Depends(require_user)):
    """导出本人 NPC 卡为酒馆 V2 JSON 格式（可直接下载/给酒馆导入）。"""
    from ... import tavern_cards, user_cards
    card = user_cards.get_user_card(user["id"], card_id)
    if not card:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    v2 = tavern_cards.user_card_to_tavern_v2(card)
    return json_response({"ok": True, "card": v2, "spec": "chara_card_v2"})


@router.get("/api/me/character-cards/{card_id}/export-png")
async def api_export_tavern_png(card_id: int, user=Depends(require_user)):
    """导出 PNG 嵌入式酒馆卡（tEXt chara chunk），可直接拖进酒馆。"""
    from fastapi.responses import Response

    from ... import tavern_cards, user_cards
    card = user_cards.get_user_card(user["id"], card_id)
    if not card:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    v2 = tavern_cards.user_card_to_tavern_v2(card)
    # 用角色头像作 PNG 底图(旧实现总输出 1x1 透明像素,酒馆里看不到立绘)。
    # 仅当头像是本地已存的 PNG 时嵌入(无 PIL 不转码);非 PNG / 取不到 → 退回最小 PNG,不报错。
    template_png = None
    avatar_path = card.get("avatar_path")
    if isinstance(avatar_path, str) and avatar_path:
        try:
            from ... import storage as _storage
            key = avatar_path.split("/api/storage/", 1)[-1]
            if key and not key.startswith(("http://", "https://", "data:", "/")):
                p = _storage.resolve_path(key)
                if p.exists():
                    raw = p.read_bytes()
                    if raw[:8] == tavern_cards.PNG_SIGNATURE:
                        template_png = raw
        except Exception:
            template_png = None
    png = tavern_cards.write_png_card(v2, template_png=template_png)
    name = (card.get("name") or f"card_{card_id}").replace(" ", "_")
    return Response(
        content=png, media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{name}.png"'},
    )


@router.post("/api/me/character-cards/import-json")
async def api_import_json_card(request: Request, user=Depends(require_user)):
    """导入 JSON 格式的酒馆角色卡（V1 / V2 均可）。

    payload: {"json": {...V2 dict...}}  或  {"json_string": "..."}
    """
    body = await request.json()
    ai_split = _truthy(body.get("ai_split"))
    from ... import tavern_cards, user_cards
    try:
        if body.get("json") is not None:
            v2 = tavern_cards.parse_card(body["json"])
        elif body.get("json_string"):
            v2 = tavern_cards.parse_card(body["json_string"])
        else:
            return json_response({"ok": False, "error": "需要 json 或 json_string 字段"}, status_code=400)
        payload = tavern_cards.tavern_to_user_card(v2)
        if ai_split:
            try:
                payload, _used = await asyncio.to_thread(
                    tavern_cards.apply_llm_structure, payload, user["id"]
                )
            except Exception:
                pass
        card = user_cards.upsert_user_card(user["id"], payload)
        return json_response({
            "ok": True, "card": card, "imported_from": "tavern_v2",
            "llm_structured": bool((payload.get("metadata") or {}).get("llm_structured_description")),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# ── 酒馆聊天记录导入 ──────────────────────────────────────────────────
@router.post("/api/me/chats/import-tavern")
async def api_import_tavern_chat(request: Request, user=Depends(require_user)):
    """导入 SillyTavern 聊天记录 JSONL，新建存档（继续这段对话）。

    payload:
      {"jsonl": "<raw JSONL text>", "title": "可选存档标题"}

    Returns:
      {"ok": true, "save_id": 123, "commits_imported": N,
       "header": {...}, "preview": [first 3 commits]}
    """
    raw_body = await request.body()
    if len(raw_body) > 16 * 1024 * 1024:
        return json_response({"ok": False, "error": "文件过大"}, status_code=400)
    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        return json_response({"ok": False, "error": "请求体须为 JSON 格式"}, status_code=400)
    from ... import tavern_chats, save_io

    jsonl_text = body.get("jsonl") or ""
    if not isinstance(jsonl_text, str) or not jsonl_text.strip():
        return json_response({"ok": False, "error": "需要 jsonl 字段（JSONL 字符串）"}, status_code=400)

    custom_title = (body.get("title") or "").strip() or None

    try:
        header, commits = tavern_chats.parse_chat_jsonl(jsonl_text)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    payload = tavern_chats.chat_to_save_payload(header, commits, title=custom_title)

    try:
        result = save_io.import_save(user["id"], payload)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    preview = [
        {"turn": c["turn_index"], "is_gm": bool(c.get("gm_output")), "preview": c.get("content_preview", "")}
        for c in commits[:3]
    ]
    return json_response({
        "ok": True,
        "save_id": result["save_id"],
        "commits_imported": result["commits_imported"],
        "header": header,
        "preview": preview,
    })
