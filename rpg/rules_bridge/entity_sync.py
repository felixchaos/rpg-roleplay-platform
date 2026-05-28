"""entity_sync.py — 实体列表构建与 active_entities 同步。"""
from __future__ import annotations


def _entities_from_room(room: dict, location_id: str = "") -> list[dict]:
    """把房间的 npcs + enemies 转成轻量 active_entity 记录。
    source = "room_data";5E 模组房间数据直接来源,稳定可信。"""
    out: list[dict] = []
    if not isinstance(room, dict):
        return out
    location = location_id or str(room.get("id") or "")
    for npc in (room.get("npcs") or []):
        if not isinstance(npc, dict):
            continue
        ent_id = str(npc.get("id") or npc.get("instance_id") or npc.get("name") or "").strip()
        if not ent_id:
            continue
        out.append({
            "id": ent_id,
            "name": npc.get("name") or ent_id,
            "kind": "npc",
            "role": npc.get("role") or npc.get("title") or "",
            "disposition": npc.get("disposition") or "neutral",
            "source": "room_data",
            "location": location,
            "status": "present",
            "stat_block_id": npc.get("stat_block_id") or "",
            "confidence": 1.0,
        })
    for foe in (room.get("enemies") or []):
        if not isinstance(foe, dict):
            continue
        ent_id = str(foe.get("id") or foe.get("instance_id") or foe.get("name") or "").strip()
        if not ent_id:
            continue
        out.append({
            "id": ent_id,
            "name": foe.get("name") or ent_id,
            "kind": "enemy",
            "role": foe.get("role") or "",
            "disposition": "hostile",
            "source": "room_data",
            "location": location,
            "status": "present",
            "stat_block_id": foe.get("stat_block_id") or "",
            "confidence": 1.0,
        })
    return out


def _entities_from_encounter(encounter: dict, location_id: str = "") -> list[dict]:
    """把 encounter.combatants 转成 active_entity 记录(仅 enemy / ally,不含 party)。
    source = "encounter";RulesEngine 启动的合法遭遇,稳定可信。"""
    out: list[dict] = []
    if not isinstance(encounter, dict):
        return out
    location = location_id or ""
    for c in (encounter.get("combatants") or []):
        if not isinstance(c, dict):
            continue
        side = str(c.get("side") or "").lower()
        if side == "party":
            continue  # 玩家自己不进 active_entities
        ent_id = str(c.get("id") or c.get("instance_id") or "").strip()
        if not ent_id:
            continue
        kind = "enemy" if side == "enemy" else "ally" if side == "ally" else "unknown"
        out.append({
            "id": ent_id,
            "name": c.get("name") or ent_id,
            "kind": kind,
            "disposition": "hostile" if kind == "enemy" else "friendly" if kind == "ally" else "unknown",
            "source": "encounter",
            "location": location,
            "status": "defeated" if c.get("defeated") else "active",
            "stat_block_id": c.get("stat_block_id") or "",
            "confidence": 1.0,
        })
    return out


def _sync_active_entities_to_scene(state, location_id: str = "") -> None:
    """把当前房间 (scene.current_room) 的 npcs/enemies 同步成 source='room_data' 实体。
    覆盖式:每次进新房间清掉旧 room_data 实体,保留 encounter / gm_provisional。"""
    scene = state.data.get("scene") or {}
    room = scene.get("current_room") or {}
    loc = location_id or scene.get("location_id") or ""
    new_room_entities = _entities_from_room(room, loc)
    state.replace_active_entities_with_source("room_data", new_room_entities)
