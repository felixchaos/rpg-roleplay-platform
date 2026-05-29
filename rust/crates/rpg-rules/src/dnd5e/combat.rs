//! combat — 战斗遭遇状态
//! 对应 Python: rpg/rules/dnd5e/combat.py

use serde_json::{json, Value};
use crate::dice;
use super::ruleset::ability_modifier;

pub fn initiative(combatants: &[Value], seed: Option<u64>) -> Result<Vec<Value>, crate::dice::DiceError> {
    let mut rolls: Vec<Value> = Vec::new();
    for (idx, c) in combatants.iter().enumerate() {
        let dex = c["abilities"]["dex"].as_i64().unwrap_or(10) as i32;
        let mod_val = ability_modifier(dex);
        let sub_seed = seed.map(|s| s + idx as u64);
        let sign = if mod_val >= 0 { "+" } else { "-" };
        let expr = format!("1d20{}{}", sign, mod_val.abs());
        let rr = dice::roll(&expr, sub_seed, false, false)?;
        rolls.push(json!({
            "id": c["id"],
            "name": c["name"],
            "side": c.get("side").cloned().unwrap_or(json!("enemy")),
            "init": rr.total,
            "dex_mod": mod_val,
            "roll": serde_json::to_value(&rr).unwrap()
        }));
    }
    // init 大→小；同分用 dex_mod；再同分保持原序（stable sort）
    rolls.sort_by(|a, b| {
        let ia = a["init"].as_i64().unwrap_or(0);
        let ib = b["init"].as_i64().unwrap_or(0);
        let da = a["dex_mod"].as_i64().unwrap_or(0);
        let db = b["dex_mod"].as_i64().unwrap_or(0);
        ib.cmp(&ia).then(db.cmp(&da))
    });
    Ok(rolls)
}

pub fn start_encounter(
    party: &[Value],
    enemies: &[Value],
    seed: Option<u64>,
    encounter_id: &str,
) -> Result<Value, crate::dice::DiceError> {
    let mut combatants: Vec<Value> = Vec::new();
    for p in party {
        combatants.push(json!({
            "id": p.get("id").cloned().unwrap_or(json!("player")),
            "name": p.get("name").cloned().unwrap_or(json!("Player")),
            "side": "party",
            "hp": p["hp"].as_i64().unwrap_or(0),
            "max_hp": p["max_hp"].as_i64().unwrap_or(0),
            "ac": p["ac"].as_i64().unwrap_or(10),
            "abilities": p.get("abilities").cloned().unwrap_or(json!({})),
            "conditions": p.get("conditions").cloned().unwrap_or(json!([])),
            "defeated": false,
            "stat_block_id": "player"
        }));
    }
    for e in enemies {
        combatants.push(json!({
            "id": e.get("id").cloned().unwrap_or(json!(null)),
            "name": e.get("name").cloned().unwrap_or(json!(null)),
            "side": "enemy",
            "hp": e["hp"].as_i64().or_else(|| e["max_hp"].as_i64()).unwrap_or(1),
            "max_hp": e["max_hp"].as_i64().or_else(|| e["hp"].as_i64()).unwrap_or(1),
            "ac": e["ac"].as_i64().unwrap_or(10),
            "abilities": e.get("abilities").cloned().unwrap_or(json!({})),
            "attacks": e.get("attacks").cloned().unwrap_or(json!([])),
            "conditions": e.get("conditions").cloned().unwrap_or(json!([])),
            "defeated": false,
            "stat_block_id": e.get("stat_block_id").cloned().unwrap_or(json!(""))
        }));
    }

    let init_order = initiative(&combatants, seed)?;
    Ok(json!({
        "active": true,
        "round": 1,
        "turn_index": 0,
        "initiative_order": init_order,
        "combatants": combatants,
        "encounter_id": encounter_id,
        "log": []
    }))
}

/// 推进到下一个未阵亡战斗员的回合。返回更新后的 encounter。
pub fn next_turn(encounter: &mut Value) -> &mut Value {
    if encounter["active"].as_bool() != Some(true) {
        return encounter;
    }
    let order_len = encounter["initiative_order"].as_array().map(|a| a.len()).unwrap_or(0);
    if order_len == 0 {
        encounter["active"] = json!(false);
        return encounter;
    }

    let mut turn_index = encounter["turn_index"].as_i64().unwrap_or(0) as usize;
    let mut round_no = encounter["round"].as_i64().unwrap_or(1) as i32;

    let combatants_by_id: std::collections::HashMap<String, Value> = encounter["combatants"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|c| c["id"].as_str().map(|id| (id.to_string(), c.clone())))
        .collect();

    let order: Vec<Value> = encounter["initiative_order"]
        .as_array()
        .cloned()
        .unwrap_or_default();

    for _ in 0..=(order_len + 1) {
        turn_index += 1;
        if turn_index >= order_len {
            turn_index = 0;
            round_no += 1;
            if round_no > 50 {
                encounter["active"] = json!(false);
                encounter["round"] = json!(round_no);
                encounter["turn_index"] = json!(turn_index);
                return encounter;
            }
        }
        let cur_id = order[turn_index]["id"].as_str().unwrap_or("").to_string();
        if let Some(comb) = combatants_by_id.get(&cur_id) {
            if comb["defeated"].as_bool() != Some(true)
                && comb["hp"].as_i64().unwrap_or(0) > 0
            {
                break;
            }
        }
    }
    encounter["turn_index"] = json!(turn_index);
    encounter["round"] = json!(round_no);
    encounter
}

/// 判断战斗是否结束。返回 (resolved, outcome)。outcome ∈ "victory"/"defeat"/"ongoing"。
pub fn is_encounter_resolved(encounter: &Value) -> (bool, &'static str) {
    if encounter["active"].as_bool() != Some(true) {
        return (true, "ongoing");
    }
    let empty = vec![];
    let combs = encounter["combatants"].as_array().unwrap_or(&empty);
    let party_alive = combs.iter().any(|c| {
        c["side"].as_str() == Some("party")
            && c["hp"].as_i64().unwrap_or(0) > 0
            && c["defeated"].as_bool() != Some(true)
    });
    let enemies_alive = combs.iter().any(|c| {
        c["side"].as_str() == Some("enemy")
            && c["hp"].as_i64().unwrap_or(0) > 0
            && c["defeated"].as_bool() != Some(true)
    });
    if !enemies_alive && party_alive {
        return (true, "victory");
    }
    if !party_alive {
        return (true, "defeat");
    }
    (false, "ongoing")
}

/// 扫描 combatants，把 hp<=0 的标 defeated。返回新被标记的 id 列表。
pub fn mark_defeated_by_hp(encounter: &mut Value) -> Vec<String> {
    let mut newly = Vec::new();
    if let Some(combs) = encounter["combatants"].as_array_mut() {
        for c in combs.iter_mut() {
            if c["hp"].as_i64().unwrap_or(0) <= 0 && c["defeated"].as_bool() != Some(true) {
                c["defeated"] = json!(true);
                if let Some(id) = c["id"].as_str() {
                    newly.push(id.to_string());
                }
            }
        }
    }
    newly
}
