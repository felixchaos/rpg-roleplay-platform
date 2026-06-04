"""coc.character — 调查员模板、技能列表、库存管理。"""

from __future__ import annotations

import copy

COC_CHARACTERISTICS = {
    "str": {"base": "3d6*5", "meaning": "力量：肌肉能力量化，决定近战伤害"},
    "con": {"base": "3d6*5", "meaning": "体质：健康、生气和活力；影响生命值"},
    "siz": {"base": "(2d6+6)*5", "meaning": "体型：身高和体重的综合；影响生命值和伤害加值"},
    "dex": {"base": "3d6*5", "meaning": "敏捷：迅捷、灵敏和肉体柔韧；决定战斗先攻顺序"},
    "app": {"base": "3d6*5", "meaning": "外貌：肉体吸引力和人格魅力；影响社交活动"},
    "int": {"base": "(2d6+6)*5", "meaning": "智力：学习能力、理解能力和分析能力；决定兴趣技能点数"},
    "pow": {"base": "3d6*5", "meaning": "意志：心意力量；决定初始理智值和魔法值；影响魔法资质"},
    "edu": {
        "base": "(2d6+6)*5",
        "meaning": "教育：正规知识掌握量；决定职业技能点数；影响初始母语值",
    },
}

COC_DERIVED = {
    "hp_formula": "floor((CON + SIZ) / 10)",
    "san_formula": "POW（初始理智值等于意志值）",
    "luck_formula": "3d6*5",
    "mp_formula": "floor(POW / 5)",
    "db_table": [
        {"min_str_siz": 2, "max_str_siz": 64, "db": "-2", "build": -2},
        {"min_str_siz": 65, "max_str_siz": 84, "db": "-1", "build": -1},
        {"min_str_siz": 85, "max_str_siz": 124, "db": "0", "build": 0},
        {"min_str_siz": 125, "max_str_siz": 164, "db": "+1d4", "build": 1},
        {"min_str_siz": 165, "max_str_siz": 204, "db": "+1d6", "build": 2},
        {"min_str_siz": 205, "max_str_siz": 284, "db": "+2d6", "build": 3},
        {"min_str_siz": 285, "max_str_siz": 364, "db": "+3d6", "build": 4},
        {"min_str_siz": 365, "max_str_siz": 444, "db": "+4d6", "build": 5},
        {"min_str_siz": 445, "max_str_siz": 524, "db": "+5d6", "build": 6},
    ],
}

COC_SKILLS: dict[str, dict] = {
    "accounting": {"name": "会计", "base": 5, "category": "occupation"},
    "animal_handling": {"name": "动物驯养", "base": 5, "category": "occupation"},
    "anthropology": {"name": "人类学", "base": 1, "category": "occupation"},
    "appraise": {"name": "估价", "base": 5, "category": "occupation"},
    "archaeology": {"name": "考古学", "base": 1, "category": "occupation"},
    "art_craft": {"name": "艺术与手艺（专攻）", "base": 5, "category": "occupation"},
    "artillery": {"name": "炮术", "base": 1, "category": "combat"},
    "charm": {"name": "取悦", "base": 15, "category": "occupation"},
    "climb": {"name": "攀爬", "base": 20, "category": "occupation"},
    "computer_use": {"name": "计算机使用", "base": 5, "category": "occupation"},
    "credit_rating": {"name": "信用评级", "base": 0, "category": "occupation"},
    "cthulhu_mythos": {"name": "克苏鲁神话", "base": 0, "category": "interest"},
    "demolitions": {"name": "爆破", "base": 1, "category": "occupation"},
    "disguise": {"name": "乔装", "base": 5, "category": "occupation"},
    "diving": {"name": "潜水", "base": 1, "category": "occupation"},
    "dodge": {"name": "闪避", "base": None, "base_from": "dex_half", "category": "combat"},
    "drive_auto": {"name": "汽车驾驶", "base": 20, "category": "occupation"},
    "electrical_repair": {"name": "电气维修", "base": 10, "category": "occupation"},
    "electronics": {"name": "电子学", "base": 1, "category": "occupation"},
    "fast_talk": {"name": "话术", "base": 5, "category": "occupation"},
    "first_aid": {"name": "急救", "base": 30, "category": "occupation"},
    "history": {"name": "历史", "base": 5, "category": "occupation"},
    "hypnosis": {"name": "催眠", "base": 1, "category": "occupation"},
    "intimidate": {"name": "恐吓", "base": 15, "category": "occupation"},
    "jump": {"name": "跳跃", "base": 20, "category": "occupation"},
    "language_other": {"name": "语言（其他）", "base": 1, "category": "occupation"},
    "language_own": {
        "name": "语言（母语）",
        "base": None,
        "base_from": "edu",
        "category": "occupation",
    },
    "law": {"name": "法律", "base": 5, "category": "occupation"},
    "library_use": {"name": "图书馆使用", "base": 20, "category": "occupation"},
    "listen": {"name": "聆听", "base": 20, "category": "occupation"},
    "locksmith": {"name": "锁匠", "base": 1, "category": "occupation"},
    "lore": {"name": "学问", "base": 1, "category": "interest"},
    "mechanical_repair": {"name": "机械维修", "base": 10, "category": "occupation"},
    "medicine": {"name": "医学", "base": 1, "category": "occupation"},
    "natural_world": {"name": "博物学", "base": 10, "category": "occupation"},
    "navigate": {"name": "导航", "base": 10, "category": "occupation"},
    "occult": {"name": "神秘学", "base": 5, "category": "occupation"},
    "operate_heavy_machinery": {"name": "操作重型机械", "base": 1, "category": "occupation"},
    "persuade": {"name": "说服", "base": 10, "category": "occupation"},
    "pilot": {"name": "驾驶（飞行器/船舶等）", "base": 1, "category": "occupation"},
    "psychoanalysis": {"name": "精神分析", "base": 1, "category": "occupation"},
    "psychology": {"name": "心理学", "base": 10, "category": "occupation"},
    "read_lips": {"name": "读唇", "base": 1, "category": "interest"},
    "ride": {"name": "骑术", "base": 5, "category": "occupation"},
    "science": {"name": "科学（专攻）", "base": 1, "category": "occupation"},
    "sleight_of_hand": {"name": "妙手", "base": 10, "category": "occupation"},
    "spot_hidden": {"name": "侦查", "base": 25, "category": "occupation"},
    "stealth": {"name": "潜行", "base": 20, "category": "occupation"},
    "survival": {"name": "生存（专攻）", "base": 10, "category": "occupation"},
    "swim": {"name": "游泳", "base": 20, "category": "occupation"},
    "throw": {"name": "投掷", "base": 20, "category": "combat"},
    "track": {"name": "追踪", "base": 10, "category": "occupation"},
    "brawl": {"name": "格斗（斗殴）", "base": 25, "category": "combat"},
    "fighting_axe": {"name": "格斗（斧）", "base": 15, "category": "combat"},
    "fighting_chainsaw": {"name": "格斗（链锯）", "base": 10, "category": "combat"},
    "fighting_flail": {"name": "格斗（连枷）", "base": 10, "category": "combat"},
    "fighting_garrote": {"name": "格斗（绞索）", "base": 15, "category": "combat"},
    "fighting_spear": {"name": "格斗（矛）", "base": 20, "category": "combat"},
    "fighting_sword": {"name": "格斗（剑）", "base": 20, "category": "combat"},
    "fighting_whip": {"name": "格斗（鞭）", "base": 5, "category": "combat"},
    "firearms_bow": {"name": "射击（弓）", "base": 15, "category": "combat"},
    "firearms_handgun": {"name": "射击（手枪）", "base": 20, "category": "combat"},
    "firearms_heavy": {"name": "射击（重武器）", "base": 10, "category": "combat"},
    "firearms_flamethrower": {"name": "射击（火焰喷射器）", "base": 10, "category": "combat"},
    "firearms_machine_gun": {"name": "射击（机枪）", "base": 10, "category": "combat"},
    "firearms_rifle": {"name": "射击（步枪）", "base": 25, "category": "combat"},
    "firearms_shotgun": {"name": "射击（霰弹枪）", "base": 25, "category": "combat"},
    "firearms_smg": {"name": "射击（冲锋枪）", "base": 15, "category": "combat"},
}

COC_SKILL_ALIASES = {
    "handgun": "firearms_handgun",
    "own_language": "language_own",
    "art_craft_photography": "art_craft",
    "other_language_latin": "language_other",
}

DEFAULT_INVESTIGATOR_INVENTORY = [
    {"name": "笔记本", "qty": 1},
    {"name": "钢笔", "qty": 1},
    {"name": "幸运硬币", "qty": 1},
]


def _derive_db(str_siz: int) -> dict:
    table = COC_DERIVED.get("db_table", [])
    for row in table:
        if row["min_str_siz"] <= str_siz <= row["max_str_siz"]:
            return {"db": row["db"], "build": row["build"]}
    return {"db": "0", "build": 0}


def make_investigator(name: str = "Investigator") -> dict:
    """创建默认调查员卡（哈维·沃尔特）。

    返回格式与 D&D character dict 兼容（有 name, hp, max_hp, abilities, inventory 等字段）。
    """
    characteristics = {
        "str": 20,
        "con": 70,
        "siz": 80,
        "dex": 55,
        "app": 80,
        "int": 85,
        "pow": 45,
        "edu": 84,
    }
    skills = {
        "credit_rating": 41,
        "library_use": 60,
        "spot_hidden": 55,
        "psychology": 50,
        "history": 55,
        "persuade": 50,
        "art_craft": 50,
        "language_other": 40,
        "language_own": 84,
        "dodge": 27,
        "brawl": 35,
        "firearms_handgun": 30,
        "stealth": 30,
        "listen": 40,
    }
    hp = (characteristics["con"] + characteristics["siz"]) // 10
    san = characteristics["pow"]
    luck = (characteristics["pow"] + characteristics["int"] + characteristics["str"]) * 2
    mp = characteristics["pow"] // 5
    db_info = _derive_db(characteristics["str"] + characteristics["siz"])

    return {
        "name": name,
        "occupation": "记者（Journalist）",
        "age": 42,
        "characteristics": characteristics,
        "skills": skills,
        "hp": hp,
        "max_hp": hp,
        "san": san,
        "max_san": 99,
        "luck": luck,
        "mp": mp,
        "max_mp": mp,
        "db": db_info["db"],
        "build": db_info["build"],
        "mov": 6,
        "ac": 0,
        "proficiency_bonus": 0,
        "level": 1,
        "class_name": "",
        "abilities": {k: v for k, v in characteristics.items()},
        "inventory": copy.deepcopy(DEFAULT_INVESTIGATOR_INVENTORY),
        "conditions": [],
        "features": [],
    }
