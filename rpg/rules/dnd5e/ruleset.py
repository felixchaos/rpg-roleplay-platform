"""
rules.dnd5e.ruleset — 5E-compatible 通用规则常量与基础函数。
"""
from __future__ import annotations


ABILITIES = ("str", "dex", "con", "int", "wis", "cha")


# 技能到属性的映射（5E 兼容）
SKILL_TO_ABILITY: dict[str, str] = {
    "acrobatics": "dex",
    "animal_handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

SKILLS = tuple(SKILL_TO_ABILITY.keys())


def ability_modifier(score: int) -> int:
    """属性修正值：(score - 10) // 2，向下取整（5E 标准）。"""
    return (int(score) - 10) // 2


def proficiency_bonus(level: int) -> int:
    """熟练加值：1-4 级+2，5-8 级+3，9-12 级+4，13-16 级+5，17-20 级+6。"""
    level = max(1, min(20, int(level)))
    return 2 + (level - 1) // 4


def normalize_skill(name: str) -> str:
    if not name:
        return ""
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")
