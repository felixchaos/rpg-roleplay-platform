//! monsters — Ash Mine 原创怪物 stat block
//! 对应 Python: rpg/rules/dnd5e/monsters.py

use once_cell::sync::Lazy;
use serde_json::{json, Value};
use std::collections::HashMap;

static STAT_BLOCKS: Lazy<HashMap<&'static str, Value>> = Lazy::new(|| {
    let mut m = HashMap::new();
    m.insert("ash_skulker", json!({
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
            {"name": "Rusty Shiv", "attack_bonus": 4, "damage": "1d4+2", "kind": "melee"}
        ],
        "tags": ["原创", "矿坑栖息者"],
        "xp": 50
    }));
    m.insert("soot_rat_swarm", json!({
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
            {"name": "Biting Tide", "attack_bonus": 2, "damage": "2d4", "kind": "melee"}
        ],
        "tags": ["原创", "群体"],
        "xp": 50
    }));
    m.insert("slag_hound", json!({
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
            {"name": "Searing Bite", "attack_bonus": 3, "damage": "1d6+1", "kind": "melee"}
        ],
        "tags": ["原创"],
        "xp": 100
    }));
    m.insert("ash_cult_warden", json!({
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
            {"name": "Iron Cudgel", "attack_bonus": 4, "damage": "1d6+2", "kind": "melee"}
        ],
        "tags": ["原创"],
        "xp": 100
    }));
    m.insert("char_acolyte_boss", json!({
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
            {"name": "Soot Bolt", "attack_bonus": 5, "damage": "2d6", "kind": "ranged"}
        ],
        "tags": ["原创", "首领"],
        "xp": 450
    }));
    m
});

/// 返回独立拷贝；调用方修改 hp 等不影响模板。
pub fn get_stat_block(stat_block_id: &str) -> Result<Value, String> {
    STAT_BLOCKS
        .get(stat_block_id)
        .cloned()
        .ok_or_else(|| format!("未知 stat_block_id: {}", stat_block_id))
}

pub fn list_stat_blocks() -> Vec<&'static str> {
    STAT_BLOCKS.keys().copied().collect()
}

/// 根据 stat_block 生成一个战斗单位实例。
pub fn build_combatant(
    stat_block_id: &str,
    instance_id: Option<&str>,
    name: Option<&str>,
) -> Result<Value, String> {
    let block = get_stat_block(stat_block_id)?;
    let inst_id = instance_id.unwrap_or(stat_block_id);
    let display_name = name
        .map(|s| s.to_string())
        .or_else(|| block["name_cn"].as_str().map(|s| s.to_string()))
        .or_else(|| block["name"].as_str().map(|s| s.to_string()))
        .unwrap_or_default();
    Ok(json!({
        "id": inst_id,
        "name": display_name,
        "side": "enemy",
        "hp": block["max_hp"],
        "max_hp": block["max_hp"],
        "ac": block["ac"],
        "abilities": block.get("abilities").cloned().unwrap_or(json!({})),
        "attacks": block.get("attacks").cloned().unwrap_or(json!([])),
        "speed": block.get("speed").cloned().unwrap_or(json!(30)),
        "stat_block_id": stat_block_id,
        "conditions": [],
        "defeated": false
    }))
}
