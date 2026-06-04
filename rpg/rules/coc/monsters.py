"""coc.monsters — CoC 怪物 stat block。"""

from __future__ import annotations

import copy

COC_STAT_BLOCKS: dict[str, dict] = {
    "deep_one": {
        "name": "深潜者（Deep One）",
        "characteristics": {"str": 70, "con": 50, "siz": 80, "dex": 50, "int": 65, "pow": 50},
        "hp": 13,
        "db": "+1d4",
        "build": 1,
        "mp": 10,
        "mov": "8 / 游泳 10",
        "armor": 1,
        "attacks": [{"name": "利爪", "skill": 45, "damage": "1d6+1d4"}],
        "sanity_loss": "0/1d6",
        "spells": [],
        "notes": "两栖生物。水下呼吸：无需外部辅助即可在水下呼吸，陆地也可呼吸。"
        "深潜者主要崇拜克苏鲁及父神达贡与母神海德拉。"
        "深潜者也可使用人类武器（如矛：1d8+DB）。",
    },
    "walter_corbitt": {
        "name": "沃尔特·科比特（Walter Corbitt）",
        "characteristics": {"str": 55, "con": 60, "siz": 50, "dex": 40, "int": 75, "pow": 80},
        "hp": 11,
        "db": "0",
        "build": 0,
        "mp": 16,
        "mov": "6",
        "armor": 0,
        "attacks": [
            {"name": "附魔匕首", "skill": 45, "damage": "1d4+2"},
            {"name": "支配术", "skill": 80, "damage": "0", "note": "与目标 POW 对抗；成功则控制目标 1 回合"},
        ],
        "sanity_loss": "0/1d6",
        "spells": ["支配术（Dominate）"],
        "notes": "亡灵复苏体。每回合自动恢复 1 HP。"
        "只有用地下室里床架上的尖木桩钉穿心脏才能永久消灭。"
        "否则被击败 1d10 回合后会重新站起。"
        "免疫毒药、眩晕和一切影响心智的法术。",
    },
}


def coc_get_stat_block(stat_block_id: str) -> dict:
    """返回独立拷贝。"""
    template = COC_STAT_BLOCKS.get(stat_block_id)
    if not template:
        raise KeyError(f"未知怪物 stat_block_id: {stat_block_id}")
    return copy.deepcopy(template)


def coc_build_combatant(
    stat_block_id: str, instance_id: str | None = None, name: str | None = None
) -> dict:
    """根据 stat block 创建战斗单位实例。"""
    block = coc_get_stat_block(stat_block_id)
    inst_id = instance_id or stat_block_id
    return {
        "id": inst_id,
        "name": name or block.get("name", stat_block_id),
        "side": "enemy",
        "hp": block["hp"],
        "max_hp": block["hp"],
        "armor": block.get("armor", 0),
        "db": block.get("db", "0"),
        "characteristics": dict(block.get("characteristics", {})),
        "attacks": list(block.get("attacks", [])),
        "sanity_loss": block.get("sanity_loss", "0/0"),
        "notes": block.get("notes", ""),
        "stat_block_id": stat_block_id,
        "conditions": [],
        "defeated": False,
        "dex": (block.get("characteristics") or {}).get("dex", 50),
    }


def coc_list_stat_blocks() -> list[str]:
    return list(COC_STAT_BLOCKS.keys())
