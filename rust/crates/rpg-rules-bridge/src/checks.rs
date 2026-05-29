//! checks — 技能检定、豁免检定、陷阱检定。
//! 对应 Python: rpg/rules_bridge/checks.py

use serde_json::{json, Value};
use rpg_rules::dnd5e::{actions, checks as rules_checks, RuleResult};
use crate::error::BridgeError;
use crate::combat::sync_player_combatant;

// ── 公开 API ─────────────────────────────────────────────────────────────

/// 对 state.data 中的 player_character 执行技能检定。
/// sets_flag：成功时写入 scene.flags.<key> = true。
#[allow(clippy::too_many_arguments)]
pub fn perform_skill_check(
    data: &mut Value,
    skill: &str,
    dc: i32,
    advantage: bool,
    disadvantage: bool,
    seed: Option<u64>,
    reason: &str,
    sets_flag: Option<&str>,
) -> Result<Value, BridgeError> {
    let pc = data["player_character"].clone();
    let result: RuleResult = rules_checks::skill_check(
        &pc,
        skill,
        dc,
        advantage,
        disadvantage,
        seed,
        pc["name"].as_str(),
        reason,
    )?;

    // 写 scene.flags
    if result.success == Some(true) {
        if let Some(flag) = sets_flag {
            data["scene"]["flags"][flag] = json!(true);
        }
    } else {
        // 检定失败 → 激活当前房间的 trigger_flag hazards
        let hazards: Vec<Value> = data["scene"]["current_room"]["hazards"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        for hazard in &hazards {
            if let Some(trigger) = hazard["trigger_flag"].as_str() {
                data["scene"]["flags"][trigger] = json!(true);
            }
        }
    }

    Ok(serde_json::to_value(&result)?)
}

/// 对 player_character 执行属性豁免。
/// fail_damage_expr：失败时附加伤害骰。
/// fail_condition：失败时附加状态条件。
#[allow(clippy::too_many_arguments)]
pub fn perform_saving_throw(
    data: &mut Value,
    ability: &str,
    dc: i32,
    advantage: bool,
    disadvantage: bool,
    seed: Option<u64>,
    reason: &str,
    fail_damage_expr: Option<&str>,
    fail_condition: Option<&str>,
) -> Result<Value, BridgeError> {
    let pc = data["player_character"].clone();
    let result: RuleResult = rules_checks::saving_throw(
        &pc,
        ability,
        dc,
        advantage,
        disadvantage,
        seed,
        pc["name"].as_str(),
        reason,
    )?;

    let mut out = serde_json::to_value(&result)?;

    if result.success != Some(true) {
        // 失败伤害
        if let Some(dmg_expr) = fail_damage_expr {
            let dmg_seed = seed.map(|s| s + 1);
            let dmg = actions::damage_roll(dmg_expr, dmg_seed, false)?;
            let dmg_amount = dmg.get("total").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
            let cur_hp = data["player_character"]["hp"].as_i64().unwrap_or(0) as i32;
            let new_hp = (cur_hp - dmg_amount).max(0);
            data["player_character"]["hp"] = json!(new_hp);
            sync_player_combatant(data);
            let max_hp = data["player_character"]["max_hp"].as_i64().unwrap_or(0);
            out["damage"] = serde_json::to_value(&dmg)?;
            out["damage_applied"] = json!(dmg_amount);
            let name = pc["name"].as_str().unwrap_or("玩家");
            if let Some(arr) = out["gm_facts"].as_array_mut() {
                arr.push(json!(format!(
                    "{} 受到 {} 点伤害（HP {}/{}）。", name, dmg_amount, new_hp, max_hp
                )));
            }
        }
        // 失败状态
        if let Some(cond) = fail_condition {
            let conds = data["player_character"]["conditions"]
                .as_array_mut();
            if let Some(arr) = conds {
                let cond_val = json!(cond);
                if !arr.contains(&cond_val) {
                    let name = pc["name"].as_str().unwrap_or("玩家").to_string();
                    arr.push(cond_val);
                    if let Some(facts) = out["gm_facts"].as_array_mut() {
                        facts.push(json!(format!("{} 获得状态：{}。", name, cond)));
                    }
                }
            }
        }
    }

    Ok(out)
}

/// 对房间内陷阱执行豁免（简化版，hazard 数据直接传入）。
pub fn trap_check(
    data: &mut Value,
    ability: &str,
    dc: i32,
    damage_expr: Option<&str>,
    condition: Option<&str>,
    trap_id: &str,
    seed: Option<u64>,
) -> Result<Value, BridgeError> {
    let reason = format!("trap:{}", trap_id);
    perform_saving_throw(
        data,
        ability,
        dc,
        false,
        false,
        seed,
        &reason,
        damage_expr,
        condition,
    )
}
