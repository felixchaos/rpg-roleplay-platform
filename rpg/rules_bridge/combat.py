"""combat.py — 战斗初始化、攻击、回合推进。"""
from __future__ import annotations

from datetime import datetime

import modules as module_registry
from rules import RulesEngine, get_engine
from rules_bridge.entity_sync import _entities_from_encounter


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


def start_encounter_by_id(state, encounter_id: str, seed: int | None = None) -> dict:
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
                  seed: int | None = None) -> dict:
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
                 attack_index: int = 0, seed: int | None = None) -> dict:
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
        target = next((c for c in encounter.get("combatants", []) if c.get("id") == target_id), None)  # type: ignore[arg-type]
        if not target:
            return {"ok": False, "error": f"未知目标：{target_id}"}

    result = engine.attack_roll(
        attacker=attacker, target=target,
        attack_bonus=int(atk_def.get("attack_bonus", 3)),
        damage_expr=str(atk_def.get("damage", "1d6")),
        seed=seed,
        attacker_name=attacker.get("name"),
        target_name=str(target.get("name")) if target.get("name") is not None else None,
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
