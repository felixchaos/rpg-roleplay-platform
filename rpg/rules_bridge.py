"""
rules_bridge.py — 规则引擎与 GameState 的接入层。

职责：
- 把模组数据加载到 game state
- 把玩家意图（来自 Demand Resolver）映射为规则动作
- 调用 RulesEngine 并把结果写回 state（经 State Gate，source="rules_engine"）
- 维护 dice_log / scene / encounter
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from rules import RulesEngine, get_engine
from rules.base import RuleResult
from rules.dnd5e.character import make_default_character, take_damage as char_take_damage, heal as char_heal
import modules as module_registry


# ── 模组操作 ────────────────────────────────────────────────────

def start_module(state, module_id: str, character_overrides: Optional[dict] = None) -> dict:
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

    # 初始化或保留角色卡：若已存在角色（有 hp/name）则保留；否则发默认 1 级冒险者
    pc = state.data.get("player_character") or {}
    if not pc.get("name") or not pc.get("hp"):
        char = make_default_character(name=(character_overrides or {}).get("name") or "Cinder", level=1)
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


# ── 规则动作执行 ────────────────────────────────────────────────

def perform_skill_check(
    state,
    skill: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: Optional[int] = None,
    reason: str = "",
    sets_flag: Optional[str] = None,
) -> dict:
    """对玩家角色执行技能检定，写入 dice_log 与 scene.flags。"""
    engine = get_engine()
    pc = state.data.get("player_character") or {}
    result = engine.skill_check(pc, skill, int(dc),
                                advantage=advantage, disadvantage=disadvantage,
                                seed=seed, actor_name=pc.get("name"), reason=reason)
    state.append_dice_log(RulesEngine.make_dice_log_entry(result, reason=reason))
    if result.success and sets_flag:
        state.set_scene_flag(sets_flag, True)
    if not result.success:
        scene = state.data.get("scene") or {}
        for hazard in (scene.get("current_room") or {}).get("hazards", []) or []:
            trigger = hazard.get("trigger_flag")
            if trigger:
                state.set_scene_flag(str(trigger), True)
                result.gm_facts.append(
                    f"检定失败触发场景风险：{hazard.get('description') or trigger}"
                )
    return result.to_dict()


def perform_saving_throw(
    state,
    ability: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: Optional[int] = None,
    reason: str = "",
    fail_damage_expr: Optional[str] = None,
    fail_condition: Optional[str] = None,
) -> dict:
    engine = get_engine()
    pc = state.data.get("player_character") or {}
    result = engine.saving_throw(pc, ability, int(dc),
                                 advantage=advantage, disadvantage=disadvantage,
                                 seed=seed, actor_name=pc.get("name"), reason=reason)
    state.append_dice_log(RulesEngine.make_dice_log_entry(result, reason=reason))
    out: dict = result.to_dict()
    if not result.success:
        if fail_damage_expr:
            damage = engine.damage_roll(fail_damage_expr, seed=(seed + 1) if isinstance(seed, int) else None)
            dmg_amount = int(damage.get("total", 0))
            actual = state.damage_player(dmg_amount, reason=reason or "saving_throw_fail")
            _sync_player_combatant(state)
            out["damage"] = damage
            out["damage_applied"] = actual
            out["gm_facts"].append(
                f"{pc.get('name','玩家')} 受到 {actual} 点伤害（HP {state.data['player_character'].get('hp')}/"
                f"{state.data['player_character'].get('max_hp')}）。"
            )
        if fail_condition:
            conds = state.data.setdefault("player_character", {}).setdefault("conditions", [])
            if fail_condition not in conds:
                conds.append(fail_condition)
                out["gm_facts"].append(f"{pc.get('name','玩家')} 获得状态：{fail_condition}。")
    return out


# ── 战斗 ────────────────────────────────────────────────────────

def _sync_player_combatant(state) -> None:
    pc = state.data.get("player_character") or {}
    encounter = state.data.get("encounter") or {}
    if not encounter.get("combatants"):
        return
    for combatant in encounter.get("combatants", []):
        if combatant.get("id") == "player":
            combatant["hp"] = int(pc.get("hp", combatant.get("hp", 0)) or 0)
            combatant["max_hp"] = int(pc.get("max_hp", combatant.get("max_hp", 0)) or 0)
            combatant["ac"] = int(pc.get("ac", combatant.get("ac", 10)) or 10)
            combatant["conditions"] = list(pc.get("conditions") or [])
            combatant["defeated"] = combatant["hp"] <= 0
            break


def start_encounter_by_id(state, encounter_id: str, seed: Optional[int] = None) -> dict:
    """根据当前模组 encounters.json 中的 id 启动战斗。"""
    engine = get_engine()
    scene = state.data.setdefault("scene", {})
    module_id = scene.get("module_id")
    if not module_id:
        return {"ok": False, "error": "未加载模组"}
    bundle = module_registry.load_module(module_id)
    enc_defs = bundle.get("encounters") or []
    enc_def = next((e for e in enc_defs if e.get("id") == encounter_id), None)
    if not enc_def:
        return {"ok": False, "error": f"未知遭遇：{encounter_id}"}

    pc = state.data.get("player_character") or {}
    party_member = dict(pc)
    party_member["id"] = "player"
    party_member.setdefault("name", pc.get("name") or "Player")
    enemies = []
    for e in enc_def.get("enemies") or []:
        comb = engine.build_combatant(e["stat_block_id"], instance_id=e.get("instance_id"), name=e.get("name"))
        enemies.append(comb)

    encounter = engine.start_encounter([party_member], enemies, seed=seed, encounter_id=encounter_id)
    encounter["definition"] = {"id": encounter_id, "name": enc_def.get("name"), "victory_flag": enc_def.get("victory_flag")}
    state.set_encounter(encounter)
    # 三层人物系统:合法 encounter 启动 → combatants 进 active_entities
    # (source='encounter'),与 room_data 实体并存。
    loc = scene.get("location_id") or enc_def.get("location_id") or ""
    for ent in _entities_from_encounter(encounter, loc):
        state.upsert_active_entity(ent)
    # 把先攻骰记入 dice_log
    for entry in encounter.get("initiative_order", []):
        state.append_dice_log({
            "id": f"dl_init_{entry.get('id')}",
            "kind": "initiative",
            "actor": entry.get("name"),
            "expression": entry.get("roll", {}).get("expression"),
            "rolls": entry.get("roll", {}).get("rolls"),
            "modifier": entry.get("dex_mod"),
            "total": entry.get("init"),
            "reason": f"先攻 - {enc_def.get('name')}",
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
    return {"ok": True, "encounter": encounter}


def player_attack(state, target_id: str, weapon_id: str = "shortsword",
                  advantage: bool = False, disadvantage: bool = False,
                  seed: Optional[int] = None) -> dict:
    """玩家对当前 encounter 中的 target 发动攻击。"""
    engine = get_engine()
    encounter = state.data.get("encounter") or {}
    if not encounter.get("active"):
        return {"ok": False, "error": "当前没有进行中的战斗"}
    target = next((c for c in encounter.get("combatants", [])
                   if c.get("id") == target_id and c.get("side") == "enemy"), None)
    if not target:
        return {"ok": False, "error": f"未找到敌方目标：{target_id}"}
    if target.get("defeated"):
        return {"ok": False, "error": f"目标已倒下：{target_id}"}

    pc = state.data.get("player_character") or {}
    weapon = (pc.get("weapons") or {}).get(weapon_id)
    if not weapon:
        return {"ok": False, "error": f"角色未持有武器：{weapon_id}"}

    result = engine.attack_roll(
        attacker=pc, target=target,
        attack_bonus=int(weapon.get("attack_bonus", 4)),
        damage_expr=str(weapon.get("damage", "1d6")),
        advantage=advantage, disadvantage=disadvantage,
        seed=seed,
        attacker_name=pc.get("name"),
        target_name=target.get("name"),
        weapon_name=weapon.get("name") or weapon_id,
    )
    # 应用 state_ops（命中扣 target HP）
    state.apply_rules_state_ops([op.to_dict() for op in result.state_ops], reason=f"player_attack {target_id}")
    state.append_dice_log(RulesEngine.make_dice_log_entry(result, reason=f"attack {target_id}"))

    # 检查 defeated；若是首领被击败，置 victory_flag
    newly = engine.mark_defeated_by_hp(encounter)
    if newly:
        result.gm_facts.append(f"{', '.join(newly)} 倒下。")

    resolved, outcome = engine.is_encounter_resolved(encounter)
    if resolved:
        encounter["active"] = False
        encounter["outcome"] = outcome
        if outcome == "victory":
            victory_flag = (encounter.get("definition") or {}).get("victory_flag")
            if victory_flag:
                state.set_scene_flag(victory_flag, True)
        result.gm_facts.append(f"战斗结束：{outcome}。")
    return {"ok": True, "result": result.to_dict(), "encounter": encounter}


def enemy_attack(state, attacker_id: str, target_id: str = "player",
                 attack_index: int = 0, seed: Optional[int] = None) -> dict:
    """敌方角色对玩家或其他战斗员发动攻击。"""
    engine = get_engine()
    encounter = state.data.get("encounter") or {}
    if not encounter.get("active"):
        return {"ok": False, "error": "当前没有进行中的战斗"}
    attacker = next((c for c in encounter.get("combatants", []) if c.get("id") == attacker_id), None)
    if not attacker or attacker.get("defeated"):
        return {"ok": False, "error": f"无效的攻击者：{attacker_id}"}
    attacks = attacker.get("attacks") or []
    if not attacks:
        return {"ok": False, "error": "攻击者没有攻击动作"}
    atk_def = attacks[max(0, min(int(attack_index), len(attacks) - 1))]
    # 目标
    if target_id == "player":
        pc = state.data.get("player_character") or {}
        target = {"name": pc.get("name") or "Player", "ac": int(pc.get("ac", 10)), "id": "player"}
    else:
        target = next((c for c in encounter.get("combatants", []) if c.get("id") == target_id), None)
        if not target:
            return {"ok": False, "error": f"未知目标：{target_id}"}

    result = engine.attack_roll(
        attacker=attacker, target=target,
        attack_bonus=int(atk_def.get("attack_bonus", 3)),
        damage_expr=str(atk_def.get("damage", "1d6")),
        seed=seed,
        attacker_name=attacker.get("name"),
        target_name=target.get("name"),
        weapon_name=atk_def.get("name") or "Attack",
    )
    if result.success and target_id == "player":
        amount = int((result.damage or {}).get("total", 0))
        actual = state.damage_player(amount, reason=f"enemy_attack {attacker_id}")
        _sync_player_combatant(state)
        result.gm_facts.append(
            f"玩家受到 {actual} 点伤害（HP {state.data['player_character'].get('hp')}/"
            f"{state.data['player_character'].get('max_hp')}）。"
        )
    elif result.success and target_id != "player":
        state.apply_rules_state_ops([op.to_dict() for op in result.state_ops], reason="enemy_attack")
    state.append_dice_log(RulesEngine.make_dice_log_entry(result, reason=f"enemy_attack {attacker_id}->{target_id}"))

    engine.mark_defeated_by_hp(encounter)
    resolved, outcome = engine.is_encounter_resolved(encounter)
    if resolved:
        encounter["active"] = False
        encounter["outcome"] = outcome
        result.gm_facts.append(f"战斗结束：{outcome}。")
    return {"ok": True, "result": result.to_dict(), "encounter": encounter}


def advance_turn(state) -> dict:
    engine = get_engine()
    encounter = state.data.get("encounter") or {}
    if not encounter.get("active"):
        return {"ok": False, "error": "没有进行中的战斗"}
    _sync_player_combatant(state)
    engine.next_turn(encounter)
    return {"ok": True, "encounter": encounter}


# ── Inventory consume (canonical) ──────────────────────────────────
# Bug 5：确定性 parser。把玩家自然语言里的"点燃/使用/消耗 N 支/份 ITEM"
# 解析成具体 inventory 物品 + 数量。不依赖 LLM；模型只负责后续叙事。

# 中文/英文消耗动词
_CONSUME_VERBS_CN = ("点燃", "使用", "消耗", "用掉", "喝", "饮", "服下", "服用",
                     "吃", "用上", "用一", "拿出", "点亮", "拿来")
_CONSUME_VERBS_EN = ("use", "consume", "burn", "light", "drink", "eat", "spend")

# 量词
_QTY_CLASSIFIERS = "(?:支|份|瓶|颗|根|片|个|只|样|件|管)"


def _zh_numeral_to_int(token: str) -> int:
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "零": 0}
    return mapping.get(token, 0)


def parse_consume_intent(text: str, character: dict) -> list[dict]:
    """从玩家文本里抽取 inventory 消耗意图。返回 list of
    {alias, qty, item_id, matched, raw}。

    确定性 parser，不依赖 LLM：
      1. 定位每个消耗动词位置（点燃/使用/消耗/use/burn 等）
      2. 在动词后窗口（≤20 字符）内寻找 inventory 真实存在的 item alias
      3. 窗口内的数字 + 量词解析为 qty（默认 1）
    """
    if not text:
        return []
    from rules.dnd5e.character import _ITEM_ALIASES, find_inventory_item, normalize_item_alias
    text_str = str(text)
    out: list[dict] = []
    seen: set[tuple] = set()

    # 按长度降序构造别名 list，确保 "healing draught" 不被 "draught" 之类的偏前匹配遮蔽
    aliases_sorted = sorted(_ITEM_ALIASES.keys(), key=lambda x: -len(x))

    # 中英文动词合并匹配
    all_verbs = list(_CONSUME_VERBS_CN) + list(_CONSUME_VERBS_EN)
    verb_pattern = "|".join(re.escape(v) for v in all_verbs)
    # 数量 token：阿拉伯数字 或 中文数字
    qty_pattern = r"(?:(\d+)|([一二两三四五六七八九十]))"

    # 步骤 1：定位每个 verb
    for verb_match in re.finditer(verb_pattern, text_str, re.IGNORECASE):
        verb_end = verb_match.end()
        # 步骤 2：动词后 20 字符窗口里找第一个 inventory 真实存在的 alias
        window = text_str[verb_end : verb_end + 24]
        found_alias = None
        alias_offset = None
        for alias in aliases_sorted:
            idx = window.lower().find(alias.lower())
            if idx >= 0:
                if alias_offset is None or idx < alias_offset:
                    found_alias = alias
                    alias_offset = idx
        if not found_alias:
            continue
        canonical = normalize_item_alias(found_alias)
        if not canonical:
            continue
        # 必须 inventory 里真有此物
        item = find_inventory_item(character, canonical)
        if item is None:
            continue
        # 步骤 3：在动词到 alias 之间解析 qty
        between = window[:alias_offset]
        qty = 1
        qm = re.search(qty_pattern, between)
        if qm:
            if qm.group(1):
                qty = int(qm.group(1))
            elif qm.group(2):
                qty = _zh_numeral_to_int(qm.group(2)) or 1

        key = (canonical, qty, verb_match.start())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "alias": found_alias,
            "item_id": canonical,
            "item_name": item.get("name"),
            "qty": qty,
            "matched": text_str[verb_match.start() : verb_end + alias_offset + len(found_alias)],
        })
    return out


def consume_item_action(state, item_id: str, qty: int = 1,
                        reason: str = "") -> dict:
    """RulesEngine consume_item 入口（chat 流程 / /api/rules/action 都用）。

    返回 {ok, result, dice_log_entry?, error}。
    成功时 player_character.inventory 已扣减，memory.resources 已同步。
    失败保持状态不变。
    """
    if not item_id:
        return {"ok": False, "error": "缺少 item_id"}
    result = state.consume_inventory_item(item_id, qty)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "consume_item 失败"}
    # 记 dice_log（虽然没掷骰，但作为 rules action 留痕）
    pc = state.data.get("player_character") or {}
    entry = {
        "kind": "consume_item",
        "actor": pc.get("name") or "player",
        "target": result.get("item_name") or result.get("item_id"),
        "expression": "",
        "rolls": [],
        "modifier": 0,
        "total": result.get("consumed"),
        "dc": None,
        "success": True,
        "reason": reason or f"消耗 {result.get('item_name')} ×{result.get('consumed')}",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "extra": {
            "item_id": result.get("item_id"),
            "qty_before": result.get("qty_before"),
            "qty_after": result.get("qty_after"),
        },
    }
    state.append_dice_log(entry)
    return {
        "ok": True,
        "result": {
            "kind": "consume_item",
            "actor": entry["actor"],
            "target": entry["target"],
            "success": True,
            "gm_facts": [
                f"{entry['actor']} 消耗 {result.get('item_name')} ×{result.get('consumed')}"
                f"（剩余 {result.get('qty_after')}）。"
            ],
            "extra": entry["extra"],
        },
        "dice_log_entry": entry,
    }


def short_rest(state, seed: Optional[int] = None) -> dict:
    """玩家短休：花生命骰回血。"""
    engine = get_engine()
    scene = state.data.get("scene") or {}
    cur_room_flags = (scene.get("current_room") or {}).get("flags") or {}
    if not cur_room_flags.get("can_short_rest"):
        return {"ok": False, "error": "当前房间不适合短休"}
    pc = state.data.setdefault("player_character", {})
    result = engine.short_rest(pc, hit_die="1d8", seed=seed)
    _sync_player_combatant(state)
    state.append_dice_log(RulesEngine.make_dice_log_entry(result, reason="short_rest"))
    return {"ok": True, "result": result.to_dict(), "player_character": pc}


def trap_check(state, room_id: str, trap_id: str, seed: Optional[int] = None) -> dict:
    """对房间内某个 hazard/陷阱解析掷豁免。"""
    engine = get_engine()
    scene = state.data.get("scene") or {}
    module_id = scene.get("module_id")
    if not module_id:
        return {"ok": False, "error": "未加载模组"}
    bundle = module_registry.load_module(module_id)
    room = next((r for r in (bundle.get("rooms") or []) if r.get("id") == room_id), None)
    if not room:
        return {"ok": False, "error": f"未知房间：{room_id}"}
    hazard = next((h for h in (room.get("hazards") or []) if h.get("id") == trap_id), None)
    if not hazard:
        return {"ok": False, "error": f"房间无此陷阱：{trap_id}"}
    save = hazard.get("save") or {}
    ability = save.get("ability", "dex")
    dc = int(save.get("dc", 10))
    damage_expr = hazard.get("damage")
    return {
        "ok": True,
        "result": perform_saving_throw(
            state, ability=ability, dc=dc, seed=seed,
            reason=f"trap:{trap_id}",
            fail_damage_expr=damage_expr,
            fail_condition=hazard.get("condition"),
        ),
    }


# ── 简易意图 → 候选规则动作 ─────────────────────────────────────

INTENT_KEYWORDS: list[tuple[str, dict]] = [
    # 潜行 / 隐蔽 / 悄悄
    (r"(悄悄|潜行|隐蔽|偷偷|不被发现|溜过去)", {"kind": "skill_check", "skill": "stealth", "dc_hint": 13}),
    # 调查 / 搜查 / 查看细节
    (r"(调查|搜查|查看|检查|搜索|翻找)", {"kind": "skill_check", "skill": "investigation", "dc_hint": 12}),
    # 察觉 / 倾听 / 留意
    (r"(察觉|留意|倾听|听一下|发现|观察)", {"kind": "skill_check", "skill": "perception", "dc_hint": 12}),
    # 攀爬 / 跳跃 / 强力
    (r"(攀爬|爬上|跳过|破门|撞开|蛮力)", {"kind": "skill_check", "skill": "athletics", "dc_hint": 12}),
    # 说服 / 谈判 / 投降 / 求饶 — 都走 Persuasion 检定 vs NPC disposition。
    # 投降不是"自动接受",而是要看敌人愿不愿放过 (敌对教派可能直接处决)。
    (r"(说服|谈判|交涉|劝说|投降|求饶|放下武器|举起?双?手|跪下投降|请降|求和)",
        {"kind": "skill_check", "skill": "persuasion", "dc_hint": 14}),
    # 欺骗
    (r"(欺骗|撒谎|装作|伪装|装成)", {"kind": "skill_check", "skill": "deception", "dc_hint": 13}),
    # 挣脱 / 反抗约束 / 摆脱抓握 — Athletics 检定 (escape grapple)
    (r"(挣脱|挣开|挣扎|甩开|摆脱抓握|脱困|逃脱束缚)",
        {"kind": "skill_check", "skill": "athletics", "dc_hint": 13}),
    # 威胁 / 恐吓
    (r"(威胁|恐吓|逼问)", {"kind": "skill_check", "skill": "intimidation", "dc_hint": 13}),
    # 攻击
    (r"(攻击|砍|射|刺|杀|出手|短弓|短剑|远程攻击|近战攻击)", {"kind": "attack", "weapon_hint": "shortsword"}),
    # 短休
    (r"(短休|休息|歇一下)", {"kind": "short_rest"}),
    # Bug 4：移动意图。匹配「沿/往/向 ... 探索/前进/走/去」等，落到当前房间的某个 exit。
    # 真实 exit 由 suggest_rule_actions 内的 _direction_to_exit() 解析；这里只是触发器。
    (r"(沿|往|向|去|前往|走向|前进|探索|进入)", {"kind": "move", "_direction_hint": True}),
]


# Bug 2 (retest)：哪些中文动词算"移动意图"。
# "观察 / 留意 / 倾听 / 检查"等是原地行为，不应触发跨房候选。
# "靠近 / 沿 / 往 / 向 / 去 / 前往 / 走向 / 进入 / 穿过"等才是真正的移动。
_MOVEMENT_VERBS = (
    "靠近", "前往", "走向", "走到", "走过", "穿过", "翻过", "回到", "进入", "退回",
    "去", "沿", "往", "向", "通过", "潜入", "溜过去", "钻进", "上到", "下到",
)


def _has_movement_intent(text: str) -> bool:
    """玩家文本是否明确包含移动到另一处的动词。
    用于决定是否做跨房间 skill check 推断（如 stealth 到相邻房间）。"""
    if not text:
        return False
    return any(v in text for v in _MOVEMENT_VERBS)


def _direction_to_exit(text: str, current_room: dict) -> str | None:
    """Bug 4：把玩家自然语言移动意图（如「沿外侧锈轨往东」「进入主井」）
    解析为当前房间真实 exit id。优先全词匹配 exit.label / id，再做 token 模糊匹配。"""
    exits = current_room.get("exits") or []
    if not exits:
        return None
    text_lower = text.lower()
    best_id = None
    best_score = 0
    for ex in exits:
        to_id = str(ex.get("to") or "")
        label = str(ex.get("label") or "")
        score = 0
        # 玩家说的字串里包含 label 主干（如"外侧锈轨"、"主井"）→ 强匹配
        for token in re.findall(r"[一-鿿]{2,}", label):
            if token in text:
                score += 3
        # 中文方向词
        for direction, exit_keywords in (
            ("东", ["东"]), ("西", ["西"]), ("北", ["北"]), ("南", ["南"]),
            ("下", ["下", "降"]), ("上", ["上", "升"]),
        ):
            if direction in text and any(kw in label for kw in exit_keywords):
                score += 2
        # 英文 fallback
        if to_id and to_id.lower() in text_lower:
            score += 5
        if score > best_score:
            best_score = score
            best_id = to_id
    return best_id if (best_id and best_score >= 2) else None


# ── 战斗意图分类器 (5E module_adventure hard gate) ───────────────────────
#
# 现场 bug:玩家在 minecart_track (room.enemies=[]、encounter.active=False) 输入
#   "借着矿车阻挡,向后拉开距离继续放箭"
# GM 直接叙事:矿车变阻挡 / 玩家被卡住 / 两名敌人贴身 / 短弓难施展 / 陷入近战威胁。
# 这违反 "RulesEngine 是唯一规则真相源"。
#
# 此函数在 GM 被调用前对玩家文本做 deterministic 分类,返回:
#   None  — 不是战斗意图 → 正常 GM 叙事流程
#   {"kind": "no_target_combat", question, options}  — 想战斗但没合法敌人 → 阻挡 GM
#   {"kind": "combat_pending_question", question, options}  — encounter 中含糊战斗 → 阻挡 GM
#
# 全部 deterministic、不调 LLM;符合 "项目接 API 玩,不能依赖模型训练" 要求。
# 调用方 (app.py chat SSE) 收到非 None 时,直接写 pending_question + 提前结束,
# **不调主 GM**,杜绝任何 "GM 把坏结果写正文" 的可能。

_ATTACK_PHRASES = (
    "攻击", "射击", "射杀", "袭击", "开火", "放箭", "出手攻击", "突袭",
)
# 紧邻武器名时,这些"软动词"才算攻击 (避免 "射" 单独命中 "反射 / 投射 / 注射" 等)
_ATTACK_SOFT_VERBS = ("射", "放", "瞄准", "拉弓", "扣弦", "投掷", "掷", "扔",
                      "砍", "刺", "戳", "杀", "斩")
_RANGED_WEAPON_HINTS = ("短弓", "长弓", "弩", "弓箭", "弓", "箭", "标枪", "飞刀", "远程")
_MELEE_WEAPON_HINTS = ("短剑", "长剑", "匕首", "战斧", "战锤", "短棍", "近战", "肉搏")
_DISENGAGE_HINTS = ("脱离", "脱身", "Disengage", "解除接触", "解开接触")
_DODGE_HINTS = ("闪避", "防御姿态", "Dodge", "招架")
# 让玩家"远离敌人"的措辞 (5E 触发借机攻击的关键)
_MOVE_AWAY_HINTS = (
    "拉开距离", "拉远距离", "拉远", "保持距离",
    "后退", "退后", "退开", "退一步", "退两步", "向后", "往后",
    "远离", "撤离", "撤退", "脱身",
)


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def _detect_ranged_weapon(text: str) -> bool:
    return _has_any(text, _RANGED_WEAPON_HINTS)


def _detect_melee_weapon(text: str) -> bool:
    return _has_any(text, _MELEE_WEAPON_HINTS)


def _detect_attack_verb(text: str) -> bool:
    """攻击意图。整词命中 (如"攻击 / 射击") 或软动词 + 武器名同现。"""
    if _has_any(text, _ATTACK_PHRASES):
        return True
    # "我用短弓射" / "拔短剑刺" 这种 — 软动词紧贴武器名
    has_soft = _has_any(text, _ATTACK_SOFT_VERBS)
    has_weapon = _detect_ranged_weapon(text) or _detect_melee_weapon(text)
    return has_soft and has_weapon


def _detect_move_away(text: str) -> bool:
    return _has_any(text, _MOVE_AWAY_HINTS)


def _detect_disengage(text: str) -> bool:
    return _has_any(text, _DISENGAGE_HINTS)


def _detect_dodge(text: str) -> bool:
    return _has_any(text, _DODGE_HINTS)


def classify_combat_intent(text: str, state) -> Optional[dict]:
    """deterministic 战斗意图分类。详见模块顶注释。

    Returns None / {"kind": "no_target_combat" | "combat_pending_question", ...}.

    设计原则:
    - 单一明确的攻击 (无 move_away) → 不拦截,让 suggest_rule_actions 走 attack
    - 仅在 (无敌人 + 想战斗) 或 (encounter 中 + 含糊战斗) 时返回阻挡块
    """
    if not text or not isinstance(text, str):
        return None

    # 只对模组场景生效;小说/自由叙事不拦截 (避免误伤纯文学描写)
    scene = state.data.get("scene") or {}
    if not scene.get("module_id"):
        return None

    has_attack = _detect_attack_verb(text)
    has_ranged = _detect_ranged_weapon(text)
    has_melee = _detect_melee_weapon(text)
    has_move_away = _detect_move_away(text)
    has_disengage = _detect_disengage(text)
    has_dodge = _detect_dodge(text)

    # 完全没有战斗 / 远程 / 近战 / 离场 信号 → 不关我事
    if not (has_attack or has_ranged or has_melee or has_move_away):
        return None

    enc = state.data.get("encounter") or {}
    encounter_active = bool(enc.get("active"))
    live_enemies = [
        c for c in (enc.get("combatants") or [])
        if c.get("side") == "enemy" and not c.get("defeated")
    ]

    current_room = scene.get("current_room") or {}
    room_enemies = current_room.get("enemies") or []

    # ──────── case 1: 想战斗 / 攻击,但当前没合法敌人 ────────
    # room.enemies=[] AND encounter.active=False AND 玩家文本里有攻击/武器
    # → GM 不允许"幻觉敌人出来"。强制 pending_question。
    wants_combat = has_attack or has_ranged or has_melee
    if wants_combat and not encounter_active and not room_enemies:
        return {
            "kind": "no_target_combat",
            "question": "你做出战斗姿态,但当下视野里没有明确的敌人或目标。要先做什么?",
            "options": [
                "仔细观察四周",
                "保持警戒慢慢推进",
                "出声试探或呼喊",
                "保持隐蔽继续探索",
            ],
            "source": "rules_engine",
            "reason": "wants_combat 但无敌人 + 无 encounter — GM 不应幻觉敌人",
            "signals": {
                "has_attack": has_attack, "has_ranged": has_ranged,
                "has_melee": has_melee, "room_enemies": len(room_enemies),
                "encounter_active": encounter_active,
            },
        }

    # ──────── case 2: encounter 中,move_away + ranged 同时出现 ────────
    # 5E:在敌人的 melee reach 内用远程武器要 disadvantage,直接移动要触发借机攻击。
    # 玩家想"边退边射"是经典含糊意图,必须明确选择策略。
    if encounter_active and live_enemies and has_move_away and has_ranged and not has_disengage:
        enemy_names = "、".join(
            (e.get("name") or e.get("id") or "敌人") for e in live_enemies[:3]
        )
        return {
            "kind": "combat_pending_question",
            "question": (
                f"敌人 ({enemy_names}) 在你的近战威胁范围 (~5 ft) 内。"
                "短弓在这个距离会有不利攻击;直接后退会触发借机攻击。请明确选一个:"
            ),
            "options": [
                "Disengage 后撤 (使用动作,免借机)",
                "直接后退 (敌人借机攻击 1 次,然后离开)",
                "切换近战 (短剑) 原地砍",
                "原地短弓射击 (不利攻击)",
            ],
            "source": "rules_engine",
            "reason": "encounter 中含糊战斗: move_away + ranged 同现",
            "signals": {
                "encounter_active": encounter_active,
                "live_enemies": len(live_enemies),
                "has_move_away": has_move_away, "has_ranged": has_ranged,
            },
        }

    # ──────── case 3: encounter 中,只 move_away 没说怎么处理敌人 ────────
    # 比 case 2 弱 — 玩家没明示用什么武器,但还是要选 Disengage / 借机后退 / 留下。
    if encounter_active and live_enemies and has_move_away and not has_disengage:
        enemy_names = "、".join(
            (e.get("name") or e.get("id") or "敌人") for e in live_enemies[:3]
        )
        return {
            "kind": "combat_pending_question",
            "question": (
                f"你想离开敌人 ({enemy_names}) 的威胁区,但没说怎么处理借机攻击:"
            ),
            "options": [
                "Disengage 后撤 (使用动作,免借机)",
                "直接后退 (承受借机攻击)",
                "原地不动改用其他动作",
            ],
            "source": "rules_engine",
            "reason": "encounter 中含糊离场",
            "signals": {
                "encounter_active": encounter_active,
                "live_enemies": len(live_enemies),
                "has_move_away": has_move_away,
            },
        }

    return None


def _triggered_encounter_id(state) -> str:
    scene = state.data.get("scene") or {}
    module_id = scene.get("module_id")
    if not module_id:
        return ""
    try:
        bundle = module_registry.load_module(module_id)
    except Exception:
        return ""
    flags = scene.get("flags") or {}
    active_flags = {k for k, v in flags.items() if v}
    location_id = scene.get("location_id")
    encounters = bundle.get("encounters") or []
    for enc in encounters:
        trigger = enc.get("trigger")
        if trigger and trigger in active_flags:
            return enc.get("id") or ""
    for enc in encounters:
        if enc.get("location_id") == location_id:
            return enc.get("id") or ""
    return ""


def _weapon_from_text(text: str) -> str:
    if any(token in text for token in ("短弓", "弓", "远程", "射", "箭")):
        return "shortbow"
    if any(token in text for token in ("短剑", "剑", "近战", "刺", "砍")):
        return "shortsword"
    return "shortsword"


def suggest_rule_actions(user_input: str, state) -> list[dict]:
    """根据用户输入文本和当前 scene 上下文，生成规则候选动作列表。

    这是简易的关键词匹配。真实场景由 LLM Demand Resolver 输出 rule_candidate_actions，
    但本函数提供 fallback 与基础线索（也方便测试）。
    """
    import re as _re
    out: list[dict] = []
    if not user_input:
        return out
    text = str(user_input)
    scene = state.data.get("scene") or {}
    current_room = scene.get("current_room") or {}
    location_id = scene.get("location_id")
    rooms_by_id: dict[str, dict] = {}
    module_id = scene.get("module_id")
    if module_id:
        try:
            rooms_by_id = {
                r.get("id"): r
                for r in (module_registry.load_module(module_id).get("rooms") or [])
                if r.get("id")
            }
        except Exception:
            rooms_by_id = {}
    for pattern, template in INTENT_KEYWORDS:
        if _re.search(pattern, text):
            action = dict(template)
            action["matched"] = pattern
            action["reason"] = f"匹配关键词「{pattern}」"
            # 如果当前房间有该 skill 的 check，借用 DC
            if action.get("kind") == "skill_check":
                target_skill = action["skill"]
                matched_check = False
                for chk in current_room.get("checks", []):
                    if chk.get("kind") == "skill_check" and chk.get("skill") == target_skill:
                        action["dc"] = chk.get("dc", action.get("dc_hint", 12))
                        action["target"] = location_id
                        action["sets_flag"] = chk.get("sets_flag")
                        action["fact"] = chk.get("fact")
                        matched_check = True
                        break
                # Bug 2 (retest)：只在玩家文本明确含『移动意图』时才跨房间扫描。
                # 之前"观察灌木"在 minecart_track（无 perception check）也触发跨房 fallback
                # → 错误地把玩家移回 mine_entrance 找 perception。
                # 现在原地无 check 时就让 GM 用默认 dc_hint 在当前房间做检定。
                if not matched_check and rooms_by_id and _has_movement_intent(text):
                    for ex in current_room.get("exits", []) or []:
                        room = rooms_by_id.get(ex.get("to"))
                        if not room:
                            continue
                        for chk in room.get("checks", []) or []:
                            if chk.get("kind") == "skill_check" and chk.get("skill") == target_skill:
                                action["dc"] = chk.get("dc", action.get("dc_hint", 12))
                                action["target"] = room.get("id")
                                action["move_to"] = room.get("id")
                                action["sets_flag"] = chk.get("sets_flag")
                                action["fact"] = chk.get("fact")
                                action["reason"] = f"{action['reason']}；目标在相邻房间「{room.get('name') or room.get('id')}」"
                                matched_check = True
                                break
                        if matched_check:
                            break
                action.setdefault("dc", action.get("dc_hint", 12))
                if not action.get("target"):
                    # 原地无 check 也要落地：target 设为当前房间，用 dc_hint 默认
                    action["target"] = location_id
            elif action.get("kind") == "attack":
                # 当前房间有敌人或战斗激活时才是合法的
                action["weapon"] = _weapon_from_text(text)
                enc = state.data.get("encounter") or {}
                if enc.get("active"):
                    enemies = [c for c in enc.get("combatants", []) if c.get("side") == "enemy" and not c.get("defeated")]
                    if enemies:
                        action["target"] = enemies[0].get("id")
                        action["target_name"] = enemies[0].get("name")
                else:
                    encounter_id = _triggered_encounter_id(state)
                    if encounter_id:
                        action["encounter_id"] = encounter_id
            elif action.get("kind") == "move":
                # Bug 4：把方向词解析到当前房间真实 exit id；无法解析就跳过。
                exit_id = _direction_to_exit(text, current_room)
                action.pop("_direction_hint", None)
                if not exit_id:
                    continue
                action["to"] = exit_id
                action["target"] = exit_id
                # 给 exit 名作为 reason 让 GM 知道这是规范化后的结果
                for ex in current_room.get("exits") or []:
                    if ex.get("to") == exit_id:
                        action["reason"] = f"方向词→出口『{ex.get('label') or exit_id}』"
                        break
            out.append(action)
    # 去重（按 kind+skill）
    seen = set()
    deduped: list[dict] = []
    for a in out:
        key = (a.get("kind"), a.get("skill"), a.get("target"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    return deduped
