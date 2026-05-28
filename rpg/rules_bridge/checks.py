"""checks.py — 技能检定、豁免检定、陷阱检定。"""
from __future__ import annotations

from typing import Optional

import modules as module_registry
from rules import RulesEngine, get_engine
from rules.base import RuleResult


def perform_skill_check(
    state,
    skill: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    seed: int | None = None,
    reason: str = "",
    sets_flag: str | None = None,
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
    seed: int | None = None,
    reason: str = "",
    fail_damage_expr: str | None = None,
    fail_condition: str | None = None,
) -> dict:
    from rules_bridge.combat import _sync_player_combatant
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


def trap_check(state, room_id: str, trap_id: str, seed: int | None = None) -> dict:
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
