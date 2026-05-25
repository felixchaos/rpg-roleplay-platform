"""
rules_bridge.py — 规则引擎与 GameState 的接入层。

职责：
- 把模组数据加载到 game state
- 把玩家意图（来自 Demand Resolver）映射为规则动作
- 调用 RulesEngine 并把结果写回 state（经 State Gate，source="rules_engine"）
- 维护 dice_log / scene / encounter
"""
from __future__ import annotations

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
    state.data["dice_log"] = []

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
    state.mark_scene_visit(location_id)
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
    engine.next_turn(encounter)
    return {"ok": True, "encounter": encounter}


def short_rest(state, seed: Optional[int] = None) -> dict:
    """玩家短休：花生命骰回血。"""
    engine = get_engine()
    scene = state.data.get("scene") or {}
    cur_room_flags = (scene.get("current_room") or {}).get("flags") or {}
    if not cur_room_flags.get("can_short_rest"):
        return {"ok": False, "error": "当前房间不适合短休"}
    pc = state.data.setdefault("player_character", {})
    result = engine.short_rest(pc, hit_die="1d8", seed=seed)
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
    # 说服 / 谈判
    (r"(说服|谈判|交涉|劝说)", {"kind": "skill_check", "skill": "persuasion", "dc_hint": 13}),
    # 欺骗
    (r"(欺骗|撒谎|装作|伪装|装成)", {"kind": "skill_check", "skill": "deception", "dc_hint": 13}),
    # 威胁 / 恐吓
    (r"(威胁|恐吓|逼问)", {"kind": "skill_check", "skill": "intimidation", "dc_hint": 13}),
    # 攻击
    (r"(攻击|砍|射|刺|杀|开战|战斗)", {"kind": "attack", "weapon_hint": "shortsword"}),
    # 短休
    (r"(短休|休息|歇一下)", {"kind": "short_rest"}),
]


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
    for pattern, template in INTENT_KEYWORDS:
        if _re.search(pattern, text):
            action = dict(template)
            action["matched"] = pattern
            action["reason"] = f"匹配关键词「{pattern}」"
            # 如果当前房间有该 skill 的 check，借用 DC
            if action.get("kind") == "skill_check":
                target_skill = action["skill"]
                for chk in current_room.get("checks", []):
                    if chk.get("kind") == "skill_check" and chk.get("skill") == target_skill:
                        action["dc"] = chk.get("dc", action.get("dc_hint", 12))
                        action["target"] = location_id
                        action["sets_flag"] = chk.get("sets_flag")
                        action["fact"] = chk.get("fact")
                        break
                action.setdefault("dc", action.get("dc_hint", 12))
            elif action.get("kind") == "attack":
                # 当前房间有敌人或战斗激活时才是合法的
                enc = state.data.get("encounter") or {}
                if enc.get("active"):
                    enemies = [c for c in enc.get("combatants", []) if c.get("side") == "enemy" and not c.get("defeated")]
                    if enemies:
                        action["target"] = enemies[0].get("id")
                        action["target_name"] = enemies[0].get("name")
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
