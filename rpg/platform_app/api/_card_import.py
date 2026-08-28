"""api/_card_import.py —— 酒馆(SillyTavern)角色卡「导入请求 → V2 卡」的统一解析。

两条导入路径共用本模块:
  · 用户角色卡  POST /api/me/character-cards/import-tavern           (card_type='pc')
  · 剧本 NPC 卡 POST /api/scripts/{id}/character-cards/import-tavern (card_type='npc')

两边对「收哪些 Content-Type / 收哪些 body 形态 / 大小上限 / 报错文案」必须逐字一致,
否则就是典型的「修 A 漏 B」——一边收 PNG 一边只收 JSON,用户在两个入口拖同一张卡
得到两种结果。解析收敛在这里,落点(写 user card / 写 NPC card)才由各自端点决定。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

MAX_IMPORT_PAYLOAD_BYTES = 16 * 1024 * 1024
_MAX_PNG_DECODED_BYTES = 10 * 1024 * 1024


def truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


async def parse_card_import_request(request: Request) -> tuple[dict[str, Any], bytes | None, bool]:
    """请求 → (V2 卡 dict, 卡自带原图 bytes 或 None, ai_split)。

    两种 Content-Type 均支持：
    A) multipart/form-data: 含 "file" 字段（.png/.json/.webp 文件）
    B) application/json payload 形态:
      - {"json": {...V2 dict...}}
      - {"json_string": "{...}"}
      - {"base64": "..."}
      - {"png_base64": "..."}

    非法输入一律抛 ValueError(调用方 `except ValueError` → 400),不在这里造响应。
    """
    from .. import tavern_cards

    content_type = request.headers.get("content-type", "")
    image_bytes: bytes | None = None  # PNG/WEBP 卡的原图，导入后存为头像 + 登记文件库

    # ── multipart/form-data（前端 importTavern(file)）─────────────
    if "multipart/form-data" in content_type:
        form = await request.form()
        ai_split = truthy(form.get("ai_split"))
        file_field = form.get("file")
        if file_field is None:
            raise ValueError("multipart 中缺少 file 字段")
        blob = await file_field.read()
        if len(blob) > MAX_IMPORT_PAYLOAD_BYTES:
            raise ValueError(f"文件过大（上限 {MAX_IMPORT_PAYLOAD_BYTES // (1024*1024)} MB）")
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
        return v2, image_bytes, ai_split

    # ── JSON body ────────────────────────────────────────────────
    body = await request.json()
    ai_split = truthy(body.get("ai_split"))
    if body.get("png_base64"):
        import base64 as _b64
        png_b64 = body["png_base64"]
        if not isinstance(png_b64, str) or len(png_b64) > MAX_IMPORT_PAYLOAD_BYTES:
            raise ValueError(f"png_base64 过大或非字符串（上限 {MAX_IMPORT_PAYLOAD_BYTES} 字节）")
        try:
            blob = _b64.b64decode(png_b64, validate=True)
        except Exception as exc:
            raise ValueError(f"png_base64 不合法：{exc}") from exc
        if len(blob) > _MAX_PNG_DECODED_BYTES:
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
        raise ValueError("需要 file(multipart) / json / json_string / base64 / png_base64 之一")
    return v2, image_bytes, ai_split
