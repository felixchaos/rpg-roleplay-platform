//! ruleset — 5E-compatible 通用规则常量与基础函数
//! 对应 Python: rpg/rules/dnd5e/ruleset.py

pub const ABILITIES: [&str; 6] = ["str", "dex", "con", "int", "wis", "cha"];

/// 技能到属性的映射（5E 兼容）
pub fn skill_to_ability(skill: &str) -> Option<&'static str> {
    match skill {
        "acrobatics"    => Some("dex"),
        "animal_handling" => Some("wis"),
        "arcana"        => Some("int"),
        "athletics"     => Some("str"),
        "deception"     => Some("cha"),
        "history"       => Some("int"),
        "insight"       => Some("wis"),
        "intimidation"  => Some("cha"),
        "investigation" => Some("int"),
        "medicine"      => Some("wis"),
        "nature"        => Some("int"),
        "perception"    => Some("wis"),
        "performance"   => Some("cha"),
        "persuasion"    => Some("cha"),
        "religion"      => Some("int"),
        "sleight_of_hand" => Some("dex"),
        "stealth"       => Some("dex"),
        "survival"      => Some("wis"),
        _ => None,
    }
}

pub const SKILLS: [&str; 18] = [
    "acrobatics", "animal_handling", "arcana", "athletics", "deception",
    "history", "insight", "intimidation", "investigation", "medicine",
    "nature", "perception", "performance", "persuasion", "religion",
    "sleight_of_hand", "stealth", "survival",
];

/// 属性修正值：(score - 10) / 2，向下取整（5E 标准）。
pub fn ability_modifier(score: i32) -> i32 {
    (score - 10).div_euclid(2)
}

/// 熟练加值：1-4 级+2，5-8 级+3，9-12 级+4，13-16 级+5，17-20 级+6。
pub fn proficiency_bonus(level: i32) -> i32 {
    let level = level.max(1).min(20);
    2 + (level - 1) / 4
}

pub fn normalize_skill(name: &str) -> String {
    name.trim().to_lowercase().replace(' ', "_").replace('-', "_")
}
