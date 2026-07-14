"""platform_app.api.me.personas —— 用户级 persona / NPC 角色卡 CRUD 端点。

persona 列表/upsert/详情/删除、character-cards 列表/upsert/详情/删除(独立于剧本存档)。
纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from .._deps import json_response, require_user
from ._shared import router


# ── 用户级 persona / character card（独立于剧本存档）─────────────
@router.get("/api/me/personas")
async def api_my_personas(user=Depends(require_user)):
    """列出本人所有玩家身份卡（杭雁菱穿越者 / 林知意信使 / ...）"""
    from ... import user_cards
    return json_response(user_cards.list_personas(user["id"]))


@router.post("/api/me/personas")
async def api_upsert_persona(request: Request, user=Depends(require_user)):
    """创建或更新 persona。传 id 强制更新某条；否则按 slug upsert。"""
    body = await request.json()
    from ... import user_cards
    try:
        return json_response({"ok": True, "persona": user_cards.upsert_persona(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/personas/{persona_id}")
async def api_get_persona(persona_id: int, user=Depends(require_user)):
    from ... import user_cards
    p = user_cards.get_persona(user["id"], persona_id)
    if not p:
        return json_response({"ok": False, "error": "persona 不存在"}, status_code=404)
    return json_response({"ok": True, "persona": p})


@router.post("/api/me/personas/{persona_id}/delete")
async def api_delete_persona(persona_id: int, user=Depends(require_user)):
    from ... import user_cards
    return json_response(user_cards.delete_persona(user["id"], persona_id))


@router.get("/api/me/character-cards")
async def api_my_character_cards(q: str | None = None, enabled: str | None = None, user=Depends(require_user)):
    """用户自创的 NPC 卡库，可挂任何剧本/存档"""
    from ... import user_cards
    enabled_only = enabled == "1"
    return json_response(user_cards.list_user_cards(user["id"], q=q or None, enabled_only=enabled_only))


@router.post("/api/me/character-cards")
async def api_upsert_character_card(request: Request, user=Depends(require_user)):
    body = await request.json()
    from ... import user_cards
    try:
        return json_response({"ok": True, "card": user_cards.upsert_user_card(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}")
async def api_get_character_card(card_id: int, user=Depends(require_user)):
    from ... import user_cards
    c = user_cards.get_user_card(user["id"], card_id)
    if not c:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    return json_response({"ok": True, "card": c})


@router.post("/api/me/character-cards/{card_id}/delete")
async def api_delete_character_card(card_id: int, user=Depends(require_user)):
    from ... import user_cards
    return json_response(user_cards.delete_user_card(user["id"], card_id))
