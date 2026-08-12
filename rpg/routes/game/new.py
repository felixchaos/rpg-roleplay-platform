"""routes.game.new —— /api/new(创建新存档 / 切换角色卡,不污染现有存档)。"""
from __future__ import annotations

from typing import Any

from fastapi import Depends
from fastapi.responses import JSONResponse

from platform_app.api._deps import json_response
from routes._deps_fastapi import get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, StateResponse
from schemas.game import NewGameRequest

from ._shared import _sanitize_payload, router


@router.post("/api/new", response_model=StateResponse, responses=COMMON_ERROR_RESPONSES)
async def api_new(
    body: NewGameRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """创建新存档。

    切换角色卡（user persona / 用户自创 NPC / 剧本预置角色）一律走这个接口，
    不会污染现有存档。优先级（高 → 低）：
      1. script_card_id + script_id  (扮演某剧本里的角色)
      2. user_card_id                 (用户自创 NPC 卡)
      3. persona_id                   (用户自己的 persona)
      4. body 里直接传 name/role/background
    """
    from app import (
        ROLES,
        GameState,
        _backup_save,
        _invalidate_user_cache,
        _payload,
        _persist_runtime_checkpoint,
        _state_by_user,
        _state_lock,
        _user_key,
    )
    body_dict = body.model_dump(exclude_none=True)
    backup = _backup_save("before_new_game") if api_user is None else None

    source_meta: dict | None = None
    source_kind = ""

    # 优先级 1：剧本预置角色卡
    script_card_id = body_dict.get("script_card_id")
    script_id = body_dict.get("script_id")
    if script_card_id and script_id and api_user:
        from platform_app import knowledge as _know
        card = _know.get_character_card(api_user["id"], int(script_id), int(script_card_id))
        if card:
            source_meta = card
            source_kind = "script_card"

    # 优先级 2：用户自创 NPC 卡
    if source_meta is None:
        user_card_id = body_dict.get("user_card_id")
        if user_card_id and api_user:
            from platform_app import user_cards as _ucards
            card = _ucards.get_user_card(api_user["id"], int(user_card_id))
            if card:
                source_meta = card
                source_kind = "user_card"

    # 优先级 3：persona
    if source_meta is None:
        persona_id = body_dict.get("persona_id")
        if persona_id and api_user:
            from platform_app import user_cards as _ucards
            persona = _ucards.get_persona(api_user["id"], int(persona_id))
            if persona:
                source_meta = persona
                source_kind = "persona"

    if source_meta:
        # 字段映射：script_card / user_card 用 identity 作 role，persona 用 role 字段
        name = source_meta.get("name") or "无名者"
        if source_kind == "persona":
            role = source_meta.get("role") or "未指定"
            background = source_meta.get("background") or "（无背景）"
        else:
            role = source_meta.get("identity") or "未指定"
            background = source_meta.get("appearance") or source_meta.get("personality") or "（来自角色卡）"
    else:
        # 通用 RPG 底座：默认 role 不再 fallback 到《我蕾穆丽娜不爱你》的『穿越者·魔女』。
        # ROLES 字典里有该剧本的 role label，作为兼容映射保留，但不再当默认值。
        role_label = (body_dict.get("role") or "").strip() or "未指定"
        role = ROLES.get(role_label, role_label)
        name = (body_dict.get("name") or "无名者").strip()
        background = (body_dict.get("background") or "").strip()

    state = GameState.new()
    state.setup_player(name, role, background)
    if source_meta:
        state.data["player"]["source_kind"] = source_kind
        state.data["player"]["source_id"] = int(source_meta.get("id") or 0)
        # 玩家游戏内头像 = 所选角色卡(PC卡)的 avatar_path,绝非账户头像。
        for field in ("appearance", "personality", "speech_style", "avatar_path"):
            if source_meta.get(field):
                state.data["player"][field] = source_meta[field]
    state.save()
    # 清掉缓存，下次 _ensure_loaded 会用新 state
    _invalidate_user_cache(api_user)
    uid = _user_key(api_user)
    with _state_lock:
        from app import _lru_set as _lru_set_inner
        _lru_set_inner(_state_by_user, uid, state)
    _persist_runtime_checkpoint(state, api_user)
    return json_response({"ok": True, "backup": backup, "state": _sanitize_payload(_payload(api_user))})
