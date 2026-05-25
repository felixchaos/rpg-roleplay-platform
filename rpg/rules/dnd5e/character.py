"""
rules.dnd5e.character — 角色卡数据结构与默认值。
"""
from __future__ import annotations

import copy
from typing import Any

from .ruleset import ABILITIES, ability_modifier, proficiency_bonus, normalize_skill, SKILL_TO_ABILITY


DEFAULT_CHARACTER: dict = {
    "name": "",
    "level": 1,
    "class_name": "scout",   # 仅作叙事标签，规则上不区分职业
    "species": "human",
    "background": "miner",
    "abilities": {"str": 10, "dex": 14, "con": 12, "int": 11, "wis": 13, "cha": 10},
    "proficiency_bonus": 2,
    "skills": {"stealth": "proficient", "investigation": "proficient", "perception": "proficient"},
    "max_hp": 12,
    "hp": 12,
    "ac": 13,
    "inventory": [
        {"id": "shortsword", "name": "Shortsword", "qty": 1, "kind": "weapon"},
        {"id": "shortbow", "name": "Shortbow", "qty": 1, "kind": "weapon"},
        {"id": "torch", "name": "Torch", "qty": 2, "kind": "gear"},
        {"id": "healing_draught", "name": "Healing Draught", "qty": 1, "kind": "consumable"},
    ],
    "conditions": [],   # 简易状态：e.g. ["poisoned", "prone"]
    "features": ["熟练：潜行 / 调查 / 察觉"],
    "weapons": {
        "shortsword": {"attack_bonus": 4, "damage": "1d6+2", "kind": "melee", "name": "Shortsword"},
        "shortbow": {"attack_bonus": 4, "damage": "1d6+2", "kind": "ranged", "name": "Shortbow"},
    },
}


def make_default_character(name: str = "Drifter", level: int = 1) -> dict:
    """生成默认 Ash Mine 探险者角色卡。"""
    char = copy.deepcopy(DEFAULT_CHARACTER)
    char["name"] = name or "Drifter"
    char["level"] = max(1, int(level))
    char["proficiency_bonus"] = proficiency_bonus(char["level"])
    # con 修正调整 max_hp（首级用类似 d8 + con）
    con_mod = ability_modifier(char["abilities"]["con"])
    base_hp = 8 + con_mod
    for lvl in range(2, char["level"] + 1):
        base_hp += 5 + con_mod
    char["max_hp"] = max(1, base_hp)
    char["hp"] = char["max_hp"]
    return char


def get_ability_score(character: dict, ability: str) -> int:
    abilities = (character or {}).get("abilities", {}) or {}
    return int(abilities.get(ability, 10))


def get_skill_proficiency(character: dict, skill: str) -> str:
    """返回 "" / "proficient" / "expertise"。"""
    skill = normalize_skill(skill)
    skills = (character or {}).get("skills", {}) or {}
    val = skills.get(skill, "")
    if isinstance(val, bool):
        return "proficient" if val else ""
    return str(val or "")


def skill_modifier(character: dict, skill: str) -> int:
    """计算技能检定 mod：属性修正 + 熟练（或专长 x2）。"""
    skill = normalize_skill(skill)
    ability = SKILL_TO_ABILITY.get(skill)
    if not ability:
        return 0
    mod = ability_modifier(get_ability_score(character, ability))
    prof = proficiency_bonus(character.get("level", 1))
    state = get_skill_proficiency(character, skill)
    if state == "expertise":
        mod += prof * 2
    elif state == "proficient":
        mod += prof
    return mod


def saving_throw_modifier(character: dict, ability: str) -> int:
    if ability not in ABILITIES:
        return 0
    mod = ability_modifier(get_ability_score(character, ability))
    saves = (character or {}).get("saves", {}) or {}
    if saves.get(ability):
        mod += proficiency_bonus(character.get("level", 1))
    return mod


def heal(character: dict, amount: int) -> int:
    """回复 HP，不超过 max_hp。返回实际回复量。"""
    amount = max(0, int(amount))
    max_hp = int(character.get("max_hp", 0) or 0)
    cur = int(character.get("hp", 0) or 0)
    new_hp = min(max_hp, cur + amount)
    character["hp"] = new_hp
    return new_hp - cur


def take_damage(character: dict, amount: int) -> int:
    """扣 HP，下限 0。返回实际扣除量。"""
    amount = max(0, int(amount))
    cur = int(character.get("hp", 0) or 0)
    new_hp = max(0, cur - amount)
    character["hp"] = new_hp
    return cur - new_hp


def has_condition(character: dict, cond: str) -> bool:
    return cond in ((character or {}).get("conditions") or [])


def add_condition(character: dict, cond: str) -> bool:
    conds = (character or {}).setdefault("conditions", [])
    if cond not in conds:
        conds.append(cond)
        return True
    return False


def remove_condition(character: dict, cond: str) -> bool:
    conds = (character or {}).setdefault("conditions", [])
    if cond in conds:
        conds.remove(cond)
        return True
    return False
