"""platform_app.api.me.cards_public —— 在线角色卡库(PC 卡:发布 / 浏览 / 完整克隆)端点。

visibility 发布开关、public 列表浏览、clone 完整复制。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from .._deps import json_response, require_user
from ._shared import router


# ── 在线角色卡库(PC 卡:发布 / 浏览 / 完整克隆)────────────────────────
@router.post("/api/me/character-cards/{card_id}/visibility")
async def api_set_card_visibility(request: Request, card_id: int, user=Depends(require_user)):
    """作者发布/取消公开自己的 PC 卡到在线角色卡库。Body: {public: bool}。"""
    from ... import user_cards
    body = await request.json()
    try:
        return json_response(user_cards.set_card_public(user["id"], card_id, bool(body.get("public"))))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.get("/api/cards/public")
async def api_list_public_cards(q: str | None = None, limit: int = 30, offset: int = 0, user=Depends(require_user)):
    """在线角色卡库:浏览他人公开的 PC 卡(只列 is_public,不含作者私密 secrets)。"""
    from ... import user_cards
    return json_response(user_cards.list_public_cards(q=q or None, limit=limit, offset=offset))


@router.post("/api/cards/public/{card_id}/clone")
async def api_clone_public_card(card_id: int, user=Depends(require_user)):
    """把一张公开 PC 卡【完整复制】进自己的卡库(复制,非指针)。"""
    from ... import user_cards
    try:
        return json_response(user_cards.clone_public_card(user["id"], card_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
