"""
rules.dnd5e.monsters — Ash Mine 原创怪物 stat block。

完全原创名称与设定，不引用任何官方 D&D / Forgotten Realms / 非 SRD IP。
属性符合 5E-compatible 思路，但故事背景独立。
"""
from __future__ import annotations

import copy


# 内部 stat block：每个键为 stat_block_id
STAT_BLOCKS: dict[str, dict] = {
    "ash_skulker": {
        "name": "Ash Skulker",
        "name_cn": "灰烬潜行者",
        "kind": "humanoid",
        "size": "small",
        "max_hp": 7,
        "hp": 7,
        "ac": 13,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 9, "wis": 8, "cha": 8},
        "speed": 30,
        "attacks": [
            {"name": "Rusty Shiv", "attack_bonus": 4, "damage": "1d4+2", "kind": "melee"},
        ],
        "tags": ["原创", "矿坑栖息者"],
        "xp": 50,
    },
    "soot_rat_swarm": {
        "name": "Soot Rat Swarm",
        "name_cn": "煤灰鼠群",
        "kind": "beast",
        "size": "medium",
        "max_hp": 14,
        "hp": 14,
        "ac": 10,
        "abilities": {"str": 9, "dex": 11, "con": 9, "int": 2, "wis": 10, "cha": 3},
        "speed": 30,
        "attacks": [
            {"name": "Biting Tide", "attack_bonus": 2, "damage": "2d4", "kind": "melee"},
        ],
        "tags": ["原创", "群体"],
        "xp": 50,
    },
    "slag_hound": {
        "name": "Slag Hound",
        "name_cn": "熔渣猎犬",
        "kind": "beast",
        "size": "medium",
        "max_hp": 11,
        "hp": 11,
        "ac": 12,
        "abilities": {"str": 13, "dex": 12, "con": 12, "int": 3, "wis": 12, "cha": 6},
        "speed": 40,
        "attacks": [
            {"name": "Searing Bite", "attack_bonus": 3, "damage": "1d6+1", "kind": "melee"},
        ],
        "tags": ["原创"],
        "xp": 100,
    },
    "ash_cult_warden": {
        "name": "Ash Cult Warden",
        "name_cn": "灰烬教典狱",
        "kind": "humanoid",
        "size": "medium",
        "max_hp": 16,
        "hp": 16,
        "ac": 13,
        "abilities": {"str": 12, "dex": 12, "con": 12, "int": 10, "wis": 11, "cha": 11},
        "speed": 30,
        "attacks": [
            {"name": "Iron Cudgel", "attack_bonus": 4, "damage": "1d6+2", "kind": "melee"},
        ],
        "tags": ["原创"],
        "xp": 100,
    },
    "char_acolyte_boss": {
        "name": "Charwoven Acolyte (Boss)",
        "name_cn": "焦痕祭司（首领）",
        "kind": "humanoid",
        "size": "medium",
        "max_hp": 32,
        "hp": 32,
        "ac": 14,
        "abilities": {"str": 11, "dex": 12, "con": 13, "int": 13, "wis": 14, "cha": 13},
        "speed": 30,
        "attacks": [
            {"name": "Ember Lash", "attack_bonus": 5, "damage": "1d8+3", "kind": "melee"},
            {"name": "Soot Bolt", "attack_bonus": 5, "damage": "2d6", "kind": "ranged"},
        ],
        "tags": ["原创", "首领"],
        "xp": 450,
    },
}


def get_stat_block(stat_block_id: str) -> dict:
    """返回独立拷贝；调用方修改 hp 等不影响模板。"""
    template = STAT_BLOCKS.get(stat_block_id)
    if not template:
        raise KeyError(f"未知 stat_block_id: {stat_block_id}")
    return copy.deepcopy(template)


def list_stat_blocks() -> list[str]:
    return list(STAT_BLOCKS.keys())


def build_combatant(stat_block_id: str, instance_id: str | None = None, name: str | None = None) -> dict:
    """根据 stat_block 生成一个战斗单位实例。"""
    block = get_stat_block(stat_block_id)
    inst_id = instance_id or stat_block_id
    return {
        "id": inst_id,
        "name": name or block.get("name_cn") or block.get("name"),
        "side": "enemy",
        "hp": block["max_hp"],
        "max_hp": block["max_hp"],
        "ac": block["ac"],
        "abilities": dict(block.get("abilities", {})),
        "attacks": list(block.get("attacks", [])),
        "speed": block.get("speed", 30),
        "stat_block_id": stat_block_id,
        "conditions": [],
        "defeated": False,
    }
