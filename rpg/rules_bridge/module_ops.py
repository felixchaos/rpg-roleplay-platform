"""module_ops.py — 模组加载与房间移动操作。"""
from __future__ import annotations

import modules as module_registry
from rules_bridge.entity_sync import _entities_from_room, _sync_active_entities_to_scene


def _room_snapshot(room: dict) -> dict:
    return {
        "id": room.get("id"),
        "name": room.get("name"),
        "name_en": room.get("name_en"),
        "description": room.get("description"),
        "exits": list(room.get("exits") or []),
        "visible_clues": list(room.get("visible_clues") or []),
        "checks": list(room.get("checks") or []),
        "hazards": list(room.get("hazards") or []),
        "npcs": list(room.get("npcs") or []),
        "enemies": list(room.get("enemies") or []),
        "loot": list(room.get("loot") or []),
        "flags": dict(room.get("flags") or {}),
    }


def start_module(state, module_id: str, character_overrides: dict | None = None) -> dict:
    """加载指定模组到 game state。重置 scene/encounter/dice_log。
    返回 {"ok": True, "scene": ..., "opening": ...}。
    """
    bundle = module_registry.load_module(module_id)
    manifest = bundle.get("manifest") or {}
    rooms = bundle.get("rooms") or []
    if not rooms:
        return {"ok": False, "error": f"模组 {module_id} 无房间数据"}

    # 选定起点
    start_id = manifest.get("starting_location") or rooms[0].get("id")
    start_room = next((r for r in rooms if r.get("id") == start_id), rooms[0])

    # 初始化或保留角色卡：若已存在角色（有 hp/name）则保留；否则发默认角色
    pc = state.data.get("player_character") or {}
    if not pc.get("name") or not pc.get("hp"):
        from rules import get_engine
        ruleset_id = (manifest.get("ruleset_meta") or {}).get("id", "dnd5e")
        engine = get_engine(ruleset_id=ruleset_id, mode=(manifest.get("ruleset_meta") or {}).get("mode", ""))

        default_char = manifest.get("default_character") or {}
        char_name = (character_overrides or {}).get("name") or default_char.get("name") or "Cinder"
        char = engine.make_default_character(name=char_name, level=1)

        if default_char:
            char.update({k: v for k, v in default_char.items() if k in ("age", "occupation", "hp", "max_hp", "san", "luck", "mp", "max_mp", "db", "build", "mov", "max_san")})
            if default_char.get("skills"):
                char.setdefault("skills", {}).update(default_char["skills"])
            if default_char.get("characteristics"):
                char.setdefault("characteristics", {}).update(default_char["characteristics"])
            if default_char.get("inventory"):
                char["inventory"] = list(default_char["inventory"])

        if character_overrides:
            for k, v in character_overrides.items():
                if k == "abilities" and isinstance(v, dict):
                    char.setdefault("abilities", {}).update(v)
                else:
                    char[k] = v
        state.set_player_character(char)

    # 设置 scene。ruleset 字段优先用 ruleset_meta（dict）便于前端展示；
    # 若 manifest 用新格式（ruleset 为 string "5e_compatible"），就归一化包成 dict。
    ruleset_field = manifest.get("ruleset_meta") or manifest.get("ruleset")
    if isinstance(ruleset_field, str):
        ruleset_field = {"id": ruleset_field, "mode": ruleset_field, "public_label": ruleset_field}
    scene = {
        "module_id": module_id,
        "location_id": start_room["id"],
        "visited_rooms": [start_room["id"]],
        "exits": list(start_room.get("exits") or []),
        "visible_clues": list(start_room.get("visible_clues") or []),
        "flags": {},
        "current_room": _room_snapshot(start_room),
        "module_manifest": {
            "id": manifest.get("id"),
            "name": manifest.get("name"),
            "name_cn": manifest.get("name_cn"),
            "tagline": manifest.get("tagline"),
            "kind": manifest.get("kind", "module_adventure"),
            "ruleset": ruleset_field,
            "context_providers": list(manifest.get("context_providers") or []),
            "retrieval_policy": dict(manifest.get("retrieval_policy") or {}),
            "gm_policy": dict(manifest.get("gm_policy") or {}),
        },
    }
    state.set_scene(scene)
    state.clear_encounter()
    # 三层人物系统:启动时只有起点房间的 npcs/enemies 进 active_entities。
    # encounter / gm_provisional 留给后续合法触发。
    state.set_active_entities(_entities_from_room(start_room, start_room["id"]))
    state.data["dice_log"] = []
    state.data["history"] = []
    state.data["turn"] = 0
    permissions = state.data.setdefault("permissions", {})
    permissions["pending_writes"] = []
    permissions["pending_questions"] = []

    # 把 player / world / memory 的非 5E 默认值替换成模组上下文，避免右侧『状态』
    # 面板继续显示 DEFAULT_STATE 里的柏林剧情默认值（图卢兹失守 / 调令伪造 等）。
    pc_now = state.data.get("player_character") or {}
    module_name = manifest.get("name_cn") or manifest.get("name") or module_id
    module_tag = manifest.get("tagline") or ""
    state.data["player"] = {
        "name": pc_now.get("name") or "Drifter",
        "role": "5E 探险者",
        "background": f"5E compatible · 五版规则兼容 · 原创规则模组『{module_name}』。{module_tag}",
        "current_location": start_room.get("name") or start_room.get("id"),
    }
    state.data["world"] = {
        "time": "灰烬山岭 · 黎明前",
        "timeline": {
            "anchor_state": "locked",
            "current_label": module_name,
            "current_phase": module_name,
            "anchor_source": "module",
            "anchor_turn": state.data.get("turn", 0),
            "pending_jump": None,
            "last_transition": None,
        },
        "known_events": [],
    }
    state.data["relationships"] = {}
    # memory 主线/当前目标也按模组覆盖
    memory_block = state.data.setdefault("memory", {})
    memory_block["main_quest"] = f"完成 {module_name} 冒险"
    memory_block["current_objective"] = manifest.get("tagline") or f"从 {start_room.get('name','起点')} 出发"
    memory_block["facts"] = []
    memory_block["notes"] = []
    memory_block["pinned"] = []
    memory_block["abilities"] = list(pc_now.get("features") or [])
    memory_block["resources"] = [
        f"{it.get('name')} ×{it.get('qty', 1)}" for it in (pc_now.get("inventory") or [])
    ]
    memory_block["items"] = []
    memory_block["last_retrieval"] = ""
    memory_block["last_context"] = {}
    memory_block["last_context_agent"] = {}
    memory_block["last_structured_updates"] = []
    # 注入开场作为 assistant 消息（不调 record_turn 避免 turn 计数 +1）
    opening = bundle.get("opening") or ""
    if opening:
        state.data.setdefault("history", []).append({"role": "assistant", "content": opening})

    return {
        "ok": True,
        "scene": scene,
        "opening": opening,
        "manifest": manifest,
        "player_character": state.data.get("player_character"),
    }


def enter_room(state, location_id: str) -> dict:
    """玩家移动到指定房间。返回新房间 snapshot 或 error。"""
    scene = state.data.setdefault("scene", {})
    module_id = scene.get("module_id")
    if not module_id:
        return {"ok": False, "error": "未加载模组"}
    bundle = module_registry.load_module(module_id)
    rooms = bundle.get("rooms") or []
    room = next((r for r in rooms if r.get("id") == location_id), None)
    if not room:
        return {"ok": False, "error": f"未知房间：{location_id}"}
    # 校验当前房间出口是否允许去 location_id
    cur_id = scene.get("location_id")
    cur_room = next((r for r in rooms if r.get("id") == cur_id), None)
    if cur_room:
        exits = cur_room.get("exits") or []
        valid_targets = {e.get("to") for e in exits}
        if location_id not in valid_targets:
            return {"ok": False, "error": f"当前房间不能直接前往 {location_id}（出口：{sorted(list(valid_targets))}）"}
        # 检查 requires
        target_exit = next((e for e in exits if e.get("to") == location_id), None)
        if target_exit and target_exit.get("requires"):
            req = str(target_exit["requires"])
            if req.startswith("flag:"):
                flag = req.split(":", 1)[1]
                if not scene.get("flags", {}).get(flag):
                    return {"ok": False, "error": f"前往 {location_id} 需要先满足条件：{flag}"}
    scene["location_id"] = location_id
    scene["exits"] = list(room.get("exits") or [])
    scene["visible_clues"] = list(room.get("visible_clues") or [])
    scene["current_room"] = _room_snapshot(room)
    state.data.setdefault("player", {})["current_location"] = room.get("name") or location_id
    state.mark_scene_visit(location_id)
    # 同步 active_entities (覆盖 source='room_data',不动 encounter / gm_provisional)
    _sync_active_entities_to_scene(state, location_id)
    return {"ok": True, "room": scene["current_room"], "scene": scene}
