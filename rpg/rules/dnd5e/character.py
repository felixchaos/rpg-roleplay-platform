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


# ── Canonical inventory operations ────────────────────────────
# player_character.inventory 是物品的唯一真相源。memory.resources 是派生展示层。

# 中英文别名 → canonical item id（用于解析玩家自然语言）
_ITEM_ALIASES: dict[str, str] = {
    # Torch
    "torch": "torch", "火把": "torch", "火炬": "torch", "提灯": "torch",
    # Healing draught
    "healing draught": "healing_draught", "healing_draught": "healing_draught",
    "急救药剂": "healing_draught", "药剂": "healing_draught", "药水": "healing_draught",
    # Shortsword
    "shortsword": "shortsword", "short sword": "shortsword",
    "短剑": "shortsword", "剑": "shortsword",
    # Shortbow
    "shortbow": "shortbow", "short bow": "shortbow",
    "短弓": "shortbow", "弓": "shortbow",
}


def normalize_item_alias(alias: str) -> str:
    """把任意玩家文本里的物品别名映射到 canonical item id。无匹配返回空串。"""
    if not alias:
        return ""
    key = str(alias).strip().lower()
    if key in _ITEM_ALIASES:
        return _ITEM_ALIASES[key]
    # 部分匹配：玩家文本片段含已知别名
    for alias_key, canonical in _ITEM_ALIASES.items():
        if alias_key in key or key in alias_key:
            return canonical
    return ""


def find_inventory_item(character: dict, alias: str) -> dict | None:
    """根据 alias 找 inventory 项。先按 canonical id 找，再 fallback 到名称模糊匹配。"""
    inventory = (character or {}).get("inventory") or []
    canonical = normalize_item_alias(alias) or alias.lower()
    for item in inventory:
        if str(item.get("id", "")).lower() == canonical:
            return item
    # name 模糊匹配
    alias_low = (alias or "").lower()
    for item in inventory:
        name_low = str(item.get("name", "")).lower()
        if name_low == alias_low or alias_low in name_low or name_low in alias_low:
            return item
    return None


def consume_inventory_item(character: dict, alias: str, qty: int = 1) -> dict:
    """从 player_character.inventory 消耗物品。

    返回 {ok, item_id, qty_before, qty_after, consumed, error}。
    qty <= 0 时 ok=False。物品数量减到 0 自动从 inventory 中移除。
    """
    qty = max(0, int(qty or 0))
    if qty == 0:
        return {"ok": False, "error": "qty 必须 > 0"}
    item = find_inventory_item(character, alias)
    if item is None:
        return {"ok": False, "error": f"背包内没有 {alias!r}",
                "item_id": "", "qty_before": 0, "qty_after": 0, "consumed": 0}
    qty_before = int(item.get("qty", 0) or 0)
    if qty_before <= 0:
        return {"ok": False, "error": f"{item.get('name')} 已耗尽",
                "item_id": item.get("id"), "qty_before": 0, "qty_after": 0, "consumed": 0}
    consumed = min(qty, qty_before)
    qty_after = qty_before - consumed
    item["qty"] = qty_after
    # qty 为 0 时从列表移除（保持 inventory 紧凑）
    if qty_after == 0:
        inventory = character.get("inventory") or []
        try:
            inventory.remove(item)
        except ValueError:
            pass
    return {
        "ok": True,
        "item_id": item.get("id"),
        "item_name": item.get("name"),
        "qty_before": qty_before,
        "qty_after": qty_after,
        "consumed": consumed,
        "error": "",
    }


def resources_from_inventory(character: dict) -> list[str]:
    """memory.resources 派生展示。inventory → ['Name ×N', ...]。"""
    inventory = (character or {}).get("inventory") or []
    out: list[str] = []
    for item in inventory:
        qty = int(item.get("qty", 0) or 0)
        if qty <= 0:
            continue
        name = item.get("name") or item.get("id") or ""
        if name:
            out.append(f"{name} ×{qty}")
    return out


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
