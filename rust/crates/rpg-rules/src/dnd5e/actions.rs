//! actions — 攻击/伤害/短休等动作
//! 对应 Python: rpg/rules/dnd5e/actions.py

use std::collections::HashMap;
use serde_json::{json, Value};
use crate::dice::{self, is_critical_hit, is_critical_miss, parse_expression, RollResult};
use super::character::{heal, take_damage};
use super::ruleset::ability_modifier;
use super::{RuleResult, StateOp};

fn roll_result_to_map(rr: &RollResult) -> HashMap<String, Value> {
    serde_json::from_value(serde_json::to_value(rr).unwrap()).unwrap()
}

/// 掷伤害骰。critical=true 时骰子数 x2（5E 兼容：双倍 dice，不双倍 mod）。
pub fn damage_roll(
    expression: &str,
    seed: Option<u64>,
    critical: bool,
) -> Result<HashMap<String, Value>, crate::dice::DiceError> {
    let rr = if critical {
        let (count, sides, mod_val) = parse_expression(expression)?;
        let sign = if mod_val >= 0 { "+" } else { "-" };
        let crit_expr = format!("{}d{}{}{}", count * 2, sides, sign, mod_val.abs());
        dice::roll(&crit_expr, seed, false, false)?
    } else {
        dice::roll(expression, seed, false, false)?
    };
    let mut d = roll_result_to_map(&rr);
    d.insert("critical".to_string(), json!(critical));
    Ok(d)
}

/// 完整攻击流程：d20+atk vs AC；命中则 damage_expr 扣 HP；自然 20 暴击。
pub fn attack_roll(
    attacker: &Value,
    target: &Value,
    attack_bonus: i32,
    damage_expr: &str,
    advantage: bool,
    disadvantage: bool,
    seed: Option<u64>,
    attacker_name: Option<&str>,
    target_name: Option<&str>,
    weapon_name: &str,
) -> Result<RuleResult, crate::dice::DiceError> {
    let bonus = attack_bonus;
    let sign = if bonus >= 0 { "+" } else { "-" };
    let expr = format!("1d20{}{}", sign, bonus.abs());
    let atk = dice::roll(&expr, seed, advantage, disadvantage)?;
    let ac = target["ac"].as_i64().unwrap_or(10) as i32;
    let actor = attacker_name
        .map(|s| s.to_string())
        .or_else(|| attacker["name"].as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| "attacker".to_string());
    let targ = target_name
        .map(|s| s.to_string())
        .or_else(|| target["name"].as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| "target".to_string());

    let mut state_ops: Vec<StateOp> = Vec::new();
    let mut gm_facts: Vec<String> = Vec::new();
    let mut damage_info: Option<HashMap<String, Value>> = None;
    let critical = is_critical_hit(&atk);
    let critical_miss = is_critical_miss(&atk);
    let mut success = false;

    if critical_miss {
        gm_facts.push(format!("{} 攻击 {} 自然 1，彻底落空。", actor, targ));
    } else if critical || atk.total >= ac {
        success = true;
        let dmg_seed = seed.map(|s| s + 1);
        let dmg = damage_roll(damage_expr, dmg_seed, critical)?;
        let dmg_amount = dmg.get("total").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
        if critical {
            gm_facts.push(format!("{} 自然 20 暴击 {}：伤害 {}（{} 暴击）。",
                actor, targ, dmg_amount, damage_expr));
        } else {
            gm_facts.push(format!("{} 用 {} 命中 {}：{} ≥ AC {}，伤害 {}。",
                actor,
                if weapon_name.is_empty() { "近战攻击" } else { weapon_name },
                targ, atk.total, ac, dmg_amount));
        }
        let target_id = target["id"].as_str().unwrap_or("").to_string();
        state_ops.push(StateOp {
            op: "subtract".to_string(),
            path: format!("_combatant.{}.hp", target_id),
            value: Some(json!(dmg_amount)),
            reason: format!("{} → {} 伤害", actor, targ),
        });
        damage_info = Some(dmg);
    } else {
        gm_facts.push(format!("{} 攻击 {} 未命中（{} < AC {}）。", actor, targ, atk.total, ac));
    }

    Ok(RuleResult {
        kind: "attack".to_string(),
        actor,
        target: targ,
        success: Some(success),
        dc: Some(ac),
        roll: roll_result_to_map(&atk),
        damage: damage_info,
        state_ops,
        gm_facts,
        extra: {
            let mut m = HashMap::new();
            m.insert("weapon".to_string(), json!(weapon_name));
            m.insert("attack_bonus".to_string(), json!(bonus));
            m.insert("critical".to_string(), json!(critical));
            m.insert("critical_miss".to_string(), json!(critical_miss));
            m
        },
    })
}

/// 直接对一个 combatant/character dict 扣 HP（陷阱伤害等用）。
pub fn apply_damage(target: &mut Value, amount: i32) -> RuleResult {
    let name = target["name"].as_str().unwrap_or("target").to_string();
    let actual = take_damage(target, amount);
    let hp = target["hp"].as_i64().unwrap_or(0);
    let max_hp = target["max_hp"].as_i64().unwrap_or(0);
    RuleResult {
        kind: "damage".to_string(),
        target: name.clone(),
        success: Some(actual > 0),
        damage: Some({
            let mut m = HashMap::new();
            m.insert("amount".to_string(), json!(actual));
            m.insert("raw".to_string(), json!(amount));
            m
        }),
        gm_facts: vec![format!("{} 受到 {} 点伤害（HP {}/{}）。", name, actual, hp, max_hp)],
        ..Default::default()
    }
}

/// 简化短休：花 1 个生命骰 + con 修正。
pub fn short_rest(
    character: &mut Value,
    hit_die: &str,
    seed: Option<u64>,
) -> Result<RuleResult, crate::dice::DiceError> {
    let con = character["abilities"]["con"].as_i64().unwrap_or(10) as i32;
    let con_mod = ability_modifier(con);
    let rr = dice::roll(hit_die, seed, false, false)?;
    let healed_raw = (rr.total + con_mod).max(1);
    let actual = heal(character, healed_raw);
    let name = character["name"].as_str().unwrap_or("player").to_string();
    let hp = character["hp"].as_i64().unwrap_or(0);
    let max_hp = character["max_hp"].as_i64().unwrap_or(0);
    Ok(RuleResult {
        kind: "short_rest".to_string(),
        actor: name.clone(),
        roll: roll_result_to_map(&rr),
        gm_facts: vec![format!(
            "{} 短休回复 {} HP（生命骰 {} + CON 修正 {}），当前 HP {}/{}。",
            name, actual, hit_die, con_mod, hp, max_hp
        )],
        extra: {
            let mut m = HashMap::new();
            m.insert("healed".to_string(), json!(actual));
            m.insert("hit_die".to_string(), json!(hit_die));
            m.insert("con_mod".to_string(), json!(con_mod));
            m
        },
        ..Default::default()
    })
}
