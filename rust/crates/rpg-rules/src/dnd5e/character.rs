//! character — 角色卡数据结构与默认值
//! 对应 Python: rpg/rules/dnd5e/character.py

use std::collections::HashMap;
use serde_json::{json, Value};
use super::ruleset::{ability_modifier, normalize_skill, proficiency_bonus, skill_to_ability, ABILITIES};

/// 默认 Ash Mine 探险者角色卡模板
pub fn default_character() -> Value {
    json!({
        "name": "",
        "level": 1,
        "class_name": "scout",
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
            {"id": "healing_draught", "name": "Healing Draught", "qty": 1, "kind": "consumable"}
        ],
        "conditions": [],
        "features": ["熟练：潜行 / 调查 / 察觉"],
        "weapons": {
            "shortsword": {"attack_bonus": 4, "damage": "1d6+2", "kind": "melee", "name": "Shortsword"},
            "shortbow": {"attack_bonus": 4, "damage": "1d6+2", "kind": "ranged", "name": "Shortbow"}
        }
    })
}

/// 生成默认 Ash Mine 探险者角色卡。
pub fn make_default_character(name: &str, level: i32) -> Value {
    let mut char = default_character();
    let name = if name.is_empty() { "Drifter" } else { name };
    char["name"] = json!(name);
    let level = level.max(1);
    char["level"] = json!(level);
    char["proficiency_bonus"] = json!(proficiency_bonus(level));

    let con = char["abilities"]["con"].as_i64().unwrap_or(12) as i32;
    let con_mod = ability_modifier(con);
    let mut base_hp = 8 + con_mod;
    for _ in 2..=level {
        base_hp += 5 + con_mod;
    }
    let max_hp = base_hp.max(1);
    char["max_hp"] = json!(max_hp);
    char["hp"] = json!(max_hp);
    char
}

pub fn get_ability_score(character: &Value, ability: &str) -> i32 {
    character["abilities"][ability].as_i64().unwrap_or(10) as i32
}

/// 返回 "" / "proficient" / "expertise"。
pub fn get_skill_proficiency(character: &Value, skill: &str) -> String {
    let skill = normalize_skill(skill);
    let v = &character["skills"][&skill];
    match v {
        Value::Bool(true) => "proficient".to_string(),
        Value::Bool(false) => String::new(),
        Value::String(s) => s.clone(),
        _ => String::new(),
    }
}

/// 计算技能检定 mod：属性修正 + 熟练（或专长 x2）。
pub fn skill_modifier(character: &Value, skill: &str) -> i32 {
    let skill = normalize_skill(skill);
    let ability = match skill_to_ability(&skill) {
        Some(a) => a,
        None => return 0,
    };
    let mut mod_val = ability_modifier(get_ability_score(character, ability));
    let prof = proficiency_bonus(character["level"].as_i64().unwrap_or(1) as i32);
    let state = get_skill_proficiency(character, &skill);
    if state == "expertise" {
        mod_val += prof * 2;
    } else if state == "proficient" {
        mod_val += prof;
    }
    mod_val
}

pub fn saving_throw_modifier(character: &Value, ability: &str) -> i32 {
    if !ABILITIES.contains(&ability) {
        return 0;
    }
    let mut mod_val = ability_modifier(get_ability_score(character, ability));
    let saves = &character["saves"];
    if saves[ability].as_bool().unwrap_or(false) {
        mod_val += proficiency_bonus(character["level"].as_i64().unwrap_or(1) as i32);
    }
    mod_val
}

/// 回复 HP，不超过 max_hp。返回实际回复量。
pub fn heal(character: &mut Value, amount: i32) -> i32 {
    let amount = amount.max(0);
    let max_hp = character["max_hp"].as_i64().unwrap_or(0) as i32;
    let cur = character["hp"].as_i64().unwrap_or(0) as i32;
    let new_hp = (cur + amount).min(max_hp);
    character["hp"] = json!(new_hp);
    new_hp - cur
}

/// 扣 HP，下限 0。返回实际扣除量。
pub fn take_damage(character: &mut Value, amount: i32) -> i32 {
    let amount = amount.max(0);
    let cur = character["hp"].as_i64().unwrap_or(0) as i32;
    let new_hp = (cur - amount).max(0);
    character["hp"] = json!(new_hp);
    cur - new_hp
}

pub fn has_condition(character: &Value, cond: &str) -> bool {
    character["conditions"]
        .as_array()
        .map(|arr| arr.iter().any(|v| v.as_str() == Some(cond)))
        .unwrap_or(false)
}

pub fn add_condition(character: &mut Value, cond: &str) -> bool {
    if !has_condition(character, cond) {
        if let Some(arr) = character["conditions"].as_array_mut() {
            arr.push(json!(cond));
            return true;
        }
    }
    false
}

pub fn remove_condition(character: &mut Value, cond: &str) -> bool {
    if let Some(arr) = character["conditions"].as_array_mut() {
        if let Some(pos) = arr.iter().position(|v| v.as_str() == Some(cond)) {
            arr.remove(pos);
            return true;
        }
    }
    false
}

// ── Item aliases ────────────────────────────────────────────────

fn item_aliases() -> &'static HashMap<&'static str, &'static str> {
    use once_cell::sync::Lazy;
    static MAP: Lazy<HashMap<&'static str, &'static str>> = Lazy::new(|| {
        let mut m = HashMap::new();
        m.insert("torch", "torch");
        m.insert("火把", "torch");
        m.insert("火炬", "torch");
        m.insert("提灯", "torch");
        m.insert("healing draught", "healing_draught");
        m.insert("healing_draught", "healing_draught");
        m.insert("急救药剂", "healing_draught");
        m.insert("药剂", "healing_draught");
        m.insert("药水", "healing_draught");
        m.insert("shortsword", "shortsword");
        m.insert("short sword", "shortsword");
        m.insert("短剑", "shortsword");
        m.insert("剑", "shortsword");
        m.insert("shortbow", "shortbow");
        m.insert("short bow", "shortbow");
        m.insert("短弓", "shortbow");
        m.insert("弓", "shortbow");
        m
    });
    &MAP
}

/// 把任意玩家文本里的物品别名映射到 canonical item id。无匹配返回空串。
pub fn normalize_item_alias(alias: &str) -> String {
    if alias.is_empty() {
        return String::new();
    }
    let key = alias.trim().to_lowercase();
    let aliases = item_aliases();
    if let Some(&v) = aliases.get(key.as_str()) {
        return v.to_string();
    }
    // 部分匹配
    for (&ak, &canonical) in aliases {
        if ak.contains(key.as_str()) || key.contains(ak) {
            return canonical.to_string();
        }
    }
    String::new()
}

/// 根据 alias 找 inventory 项索引。
pub fn find_inventory_item_index(character: &Value, alias: &str) -> Option<usize> {
    let inventory = character["inventory"].as_array()?;
    let canonical = {
        let c = normalize_item_alias(alias);
        if c.is_empty() { alias.to_lowercase() } else { c }
    };
    // exact id match
    if let Some(pos) = inventory.iter().position(|item| {
        item["id"].as_str().map(|s| s.to_lowercase()) == Some(canonical.clone())
    }) {
        return Some(pos);
    }
    // name fuzzy match
    let alias_low = alias.to_lowercase();
    inventory.iter().position(|item| {
        let name_low = item["name"].as_str().unwrap_or("").to_lowercase();
        name_low == alias_low || name_low.contains(&alias_low) || alias_low.contains(&name_low)
    })
}

pub fn find_inventory_item<'a>(character: &'a Value, alias: &str) -> Option<&'a Value> {
    let idx = find_inventory_item_index(character, alias)?;
    character["inventory"].get(idx)
}

/// 从 player_character.inventory 消耗物品。
pub fn consume_inventory_item(character: &mut Value, alias: &str, qty: i32) -> Value {
    let qty = qty.max(0);
    if qty == 0 {
        return json!({"ok": false, "error": "qty 必须 > 0"});
    }
    let idx = match find_inventory_item_index(character, alias) {
        Some(i) => i,
        None => return json!({
            "ok": false,
            "error": format!("背包内没有 {:?}", alias),
            "item_id": "", "qty_before": 0, "qty_after": 0, "consumed": 0
        }),
    };

    let qty_before = character["inventory"][idx]["qty"].as_i64().unwrap_or(0) as i32;
    if qty_before <= 0 {
        let name = character["inventory"][idx]["name"].as_str().unwrap_or("").to_string();
        let item_id = character["inventory"][idx]["id"].as_str().unwrap_or("").to_string();
        return json!({
            "ok": false,
            "error": format!("{} 已耗尽", name),
            "item_id": item_id, "qty_before": 0, "qty_after": 0, "consumed": 0
        });
    }

    let consumed = qty.min(qty_before);
    let qty_after = qty_before - consumed;
    let item_id = character["inventory"][idx]["id"].as_str().unwrap_or("").to_string();
    let item_name = character["inventory"][idx]["name"].as_str().unwrap_or("").to_string();

    character["inventory"][idx]["qty"] = json!(qty_after);
    if qty_after == 0 {
        if let Some(arr) = character["inventory"].as_array_mut() {
            arr.remove(idx);
        }
    }

    json!({
        "ok": true,
        "item_id": item_id,
        "item_name": item_name,
        "qty_before": qty_before,
        "qty_after": qty_after,
        "consumed": consumed,
        "error": ""
    })
}

/// memory.resources 派生展示。inventory → ["Name ×N", ...]。
pub fn resources_from_inventory(character: &Value) -> Vec<String> {
    let empty = vec![];
    let inventory = character["inventory"].as_array().unwrap_or(&empty);
    inventory.iter().filter_map(|item| {
        let qty = item["qty"].as_i64().unwrap_or(0);
        if qty <= 0 { return None; }
        let name = item["name"].as_str().or_else(|| item["id"].as_str()).unwrap_or("");
        if name.is_empty() { return None; }
        Some(format!("{} ×{}", name, qty))
    }).collect()
}
