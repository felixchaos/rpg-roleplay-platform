//! checks — 技能检定与豁免。纯函数。
//! 对应 Python: rpg/rules/dnd5e/checks.py

use std::collections::HashMap;
use serde_json::{json, Value};
use crate::dice::{self, RollResult};
use super::character::{saving_throw_modifier, skill_modifier};
use super::ruleset::{normalize_skill, ABILITIES};
use super::RuleResult;

fn roll_result_to_map(rr: &RollResult) -> HashMap<String, Value> {
    serde_json::from_value(serde_json::to_value(rr).unwrap()).unwrap()
}

pub fn skill_check(
    character: &Value,
    skill: &str,
    dc: i32,
    advantage: bool,
    disadvantage: bool,
    seed: Option<u64>,
    actor_name: Option<&str>,
    reason: &str,
) -> Result<RuleResult, crate::dice::DiceError> {
    let skill = normalize_skill(skill);
    let mod_val = skill_modifier(character, &skill);
    let sign = if mod_val >= 0 { "+" } else { "-" };
    let expr = format!("1d20{}{}", sign, mod_val.abs());
    let rr = dice::roll(&expr, seed, advantage, disadvantage)?;
    let success = rr.total >= dc;

    let actor = actor_name
        .map(|s| s.to_string())
        .or_else(|| character["name"].as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| "player".to_string());
    let fact_skill = skill.replace('_', " ");
    let gm_fact = if success {
        format!("{} 的 {} 检定成功（{} ≥ DC {}）。", actor, fact_skill, rr.total, dc)
    } else {
        format!("{} 的 {} 检定失败（{} < DC {}）。", actor, fact_skill, rr.total, dc)
    };

    Ok(RuleResult {
        kind: "skill_check".to_string(),
        actor: actor.clone(),
        target: reason.to_string(),
        success: Some(success),
        dc: Some(dc),
        roll: roll_result_to_map(&rr),
        gm_facts: vec![gm_fact],
        extra: {
            let mut m = HashMap::new();
            m.insert("skill".to_string(), json!(skill));
            m.insert("modifier".to_string(), json!(mod_val));
            m.insert("reason".to_string(), json!(reason));
            m
        },
        ..Default::default()
    })
}

pub fn saving_throw(
    character: &Value,
    ability: &str,
    dc: i32,
    advantage: bool,
    disadvantage: bool,
    seed: Option<u64>,
    actor_name: Option<&str>,
    reason: &str,
) -> Result<RuleResult, crate::dice::DiceError> {
    let ability = ability.to_lowercase();
    if !ABILITIES.contains(&ability.as_str()) {
        // Return an error by reusing DiceError::ParseError as a proxy
        return Err(crate::dice::DiceError::ParseError(format!("未知属性：{}", ability)));
    }
    let mod_val = saving_throw_modifier(character, &ability);
    let sign = if mod_val >= 0 { "+" } else { "-" };
    let expr = format!("1d20{}{}", sign, mod_val.abs());
    let rr = dice::roll(&expr, seed, advantage, disadvantage)?;
    let success = rr.total >= dc;

    let actor = actor_name
        .map(|s| s.to_string())
        .or_else(|| character["name"].as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| "player".to_string());
    let gm_fact = if success {
        format!("{} 通过了 {} 豁免（{} ≥ DC {}）。", actor, ability.to_uppercase(), rr.total, dc)
    } else {
        format!("{} 未能通过 {} 豁免（{} < DC {}）。", actor, ability.to_uppercase(), rr.total, dc)
    };

    Ok(RuleResult {
        kind: "saving_throw".to_string(),
        actor: actor.clone(),
        target: reason.to_string(),
        success: Some(success),
        dc: Some(dc),
        roll: roll_result_to_map(&rr),
        gm_facts: vec![gm_fact],
        extra: {
            let mut m = HashMap::new();
            m.insert("ability".to_string(), json!(ability));
            m.insert("modifier".to_string(), json!(mod_val));
            m.insert("reason".to_string(), json!(reason));
            m
        },
        ..Default::default()
    })
}
