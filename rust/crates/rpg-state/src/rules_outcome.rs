//! rules_outcome.rs — RulesEngine 算完的结果写回 GameState 的 helper。
//!
//! Wave 5-B / P1-5
//! ================
//! 对应 Python: `rpg/state/_mixins/apply_ops.py::apply_rules_state_ops`
//! + Python rules_bridge dispatcher 在 attack/check 之后调 `state.apply_rules_state_ops`。
//!
//! 在 Rust 侧之前的状况:
//! - `rpg_rules::dnd5e::actions::attack_roll` 算出 `state_ops = [StateOp{ op:"subtract",
//!   path:"_combatant.<id>.hp", value:dmg }]`。
//! - `rpg-rules-bridge::combat::apply_combatant_ops` 只处理 `_combatant.*` 这一类
//!   path,把数值写到 encounter.combatants。
//! - **任何走非 `_combatant.*` path 的 state_op(比如 player_character.hp /
//!   player_character.inventory[*].qty)直接被吃掉,导致玩家收到 12 点伤害但
//!   `player_character.hp` 始终满血。**
//!
//! 这个模块给 dispatcher 提供:
//! - [`apply_hp_delta`] — 给玩家加/减 HP(并夹在 0..=max_hp)。
//! - [`apply_inventory_change`] — 改 `player_character.inventory` 里某 item 的 qty。
//! - [`apply_rules_outcome`] — 把 RulesEngine 返回的通用 `(op, path, value)` 列表
//!   统一写回 state(`_combatant.*` 继续 fall through 给 bridge 处理)。
//!
//! 每个 helper 都有两个层级的入口:
//! - `*_data(&mut GameStateData, ...)` — 直接给 rpg-rules-bridge 这种持
//!   GameStateData 的调用方,避免反向依赖 rpg-state::GameState。
//! - `*(&mut GameState, ...)` — 给上层(routes / agents)用,会自动 `touch()`。
//!
//! 这里只调 typed_path::set_path,不走 op-validation gate —— 调用方是 rules engine,
//! 默认可信。对应 Python 侧 `source="rules_engine"` bypass。

use rpg_schemas::GameStateData;
use serde_json::{json, Value};

use crate::state::{GameState, StateError};
use crate::typed_path;

/// 通用 op,与 `rpg_rules::dnd5e::StateOp` 字段对齐,但不依赖 rpg-rules crate
/// (避免 rpg-state → rpg-rules 反向依赖)。
#[derive(Debug, Clone)]
pub struct RulesOp<'a> {
    pub op: &'a str,
    pub path: &'a str,
    pub value: Option<&'a Value>,
}

// ── GameStateData 入口(rules-bridge dispatcher 用)────────────────────────

/// 把 RulesEngine 算出的 state_ops 写回 GameStateData。
///
/// 返回 `(applied, skipped)`:
/// - `applied` = 成功落地的 op 的人类描述(便于 GM 日志)。
/// - `skipped` = path 以 `_combatant.` 起头的 op(交给 rpg-rules-bridge::combat
///   里的 `apply_combatant_ops` 处理 encounter 内部),不是错误。
///
/// 不返回 StateError;单条 op 失败只在 applied 里记一条 `failed:` 字符串,
/// 整批继续 —— 与 Python `apply_rules_state_ops` 的"宽容模式"语义一致。
pub fn apply_rules_outcome_data(
    data: &mut GameStateData,
    ops: &[RulesOp<'_>],
) -> (Vec<String>, Vec<String>) {
    let mut applied: Vec<String> = Vec::new();
    let mut skipped: Vec<String> = Vec::new();

    for op in ops {
        let path = op.path;
        if path.is_empty() {
            continue;
        }
        if path.starts_with("_combatant.") {
            skipped.push(path.to_string());
            continue;
        }
        let kind = op.op;
        let value = op.value.cloned().unwrap_or(Value::Null);

        let cleaned = crate::path::clean_path(path);
        let result: Result<String, StateError> = match kind {
            "subtract" => {
                let cur = typed_path::get_path(data, &cleaned)
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                let delta = value.as_i64().unwrap_or(0);
                let next = (cur - delta).max(0);
                typed_path::set_path(data, &cleaned, json!(next))
                    .map(|_| format!("{path}={next}"))
            }
            "add" => {
                let cur = typed_path::get_path(data, &cleaned)
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                let delta = value.as_i64().unwrap_or(0);
                let next = cur + delta;
                typed_path::set_path(data, &cleaned, json!(next))
                    .map(|_| format!("{path}={next}"))
            }
            "append" => typed_path::append_path(data, &cleaned, value.clone())
                .map(|_| format!("append {path}")),
            _ => typed_path::set_path(data, &cleaned, value.clone())
                .map(|_| format!("set {path}={value}")),
        };
        match result {
            Ok(msg) => applied.push(msg),
            Err(e) => applied.push(format!("failed {path}: {e}")),
        }
    }

    (applied, skipped)
}

/// 改 `player_character.hp`,夹在 `0..=max_hp` 内,GameStateData 版本。
pub fn apply_hp_delta_data(data: &mut GameStateData, delta: i32) -> Result<i32, StateError> {
    let cur = typed_path::get_path(data, "player_character.hp")
        .and_then(|v| v.as_i64())
        .unwrap_or(0) as i32;
    let max = typed_path::get_path(data, "player_character.max_hp")
        .and_then(|v| v.as_i64())
        .unwrap_or(i64::MAX) as i32;
    let raw = cur.saturating_add(delta);
    let clamped = raw.clamp(0, max.max(0));
    typed_path::set_path(data, "player_character.hp", json!(clamped))?;
    Ok(clamped)
}

/// 改 `player_character.inventory` 里 `id == item_id` 的 `qty`,GameStateData 版本。
///
/// - 找不到 item 时不报错,返回 `Ok(None)`。
/// - 找到时返回 `Ok(Some(new_qty))`,夹在 `0..` 范围内。
pub fn apply_inventory_change_data(
    data: &mut GameStateData,
    item_id: &str,
    delta: i32,
) -> Result<Option<i32>, StateError> {
    let inv_value = match typed_path::get_path(data, "player_character.inventory") {
        Some(v) => v,
        None => return Ok(None),
    };
    let arr = match inv_value.as_array() {
        Some(a) => a.clone(),
        None => return Ok(None),
    };
    let mut found: Option<(usize, i32)> = None;
    for (i, item) in arr.iter().enumerate() {
        if item.get("id").and_then(|v| v.as_str()) == Some(item_id) {
            let q = item.get("qty").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
            found = Some((i, q));
            break;
        }
    }
    let Some((idx, cur_qty)) = found else {
        return Ok(None);
    };
    let next_qty = (cur_qty + delta).max(0);
    let path = format!("player_character.inventory[{idx}].qty");
    typed_path::set_path(data, &path, json!(next_qty))?;
    Ok(Some(next_qty))
}

// ── GameState 入口(routes / agents 用,自动 touch)──────────────────────

/// 见 [`apply_rules_outcome_data`],GameState 包装版,自动 `touch()`。
pub fn apply_rules_outcome(
    state: &mut GameState,
    ops: &[RulesOp<'_>],
) -> (Vec<String>, Vec<String>) {
    let (applied, skipped) = apply_rules_outcome_data(&mut state.data, ops);
    if !applied.is_empty() {
        state.touch();
    }
    (applied, skipped)
}

/// 见 [`apply_hp_delta_data`],GameState 包装版,自动 `touch()`。
pub fn apply_hp_delta(state: &mut GameState, delta: i32) -> Result<i32, StateError> {
    let new_hp = apply_hp_delta_data(&mut state.data, delta)?;
    state.touch();
    Ok(new_hp)
}

/// 见 [`apply_inventory_change_data`],GameState 包装版,自动 `touch()`。
pub fn apply_inventory_change(
    state: &mut GameState,
    item_id: &str,
    delta: i32,
) -> Result<Option<i32>, StateError> {
    let r = apply_inventory_change_data(&mut state.data, item_id, delta)?;
    if r.is_some() {
        state.touch();
    }
    Ok(r)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::default_state;
    use serde_json::json;

    fn make_state_with_hp(hp: i32, max_hp: i32) -> GameState {
        let mut s = GameState::default();
        s.user_id = "test".into();
        s.data = serde_json::from_value(default_state()).unwrap();
        s.data.player_character.hp = hp;
        s.data.player_character.max_hp = max_hp;
        s
    }

    #[test]
    fn hp_delta_subtract() {
        let mut s = make_state_with_hp(20, 30);
        let new_hp = apply_hp_delta(&mut s, -12).unwrap();
        assert_eq!(new_hp, 8);
        assert_eq!(s.data.player_character.hp, 8);
    }

    #[test]
    fn hp_delta_clamps_to_zero() {
        let mut s = make_state_with_hp(5, 30);
        let new_hp = apply_hp_delta(&mut s, -100).unwrap();
        assert_eq!(new_hp, 0);
        assert_eq!(s.data.player_character.hp, 0);
    }

    #[test]
    fn hp_delta_clamps_to_max() {
        let mut s = make_state_with_hp(20, 30);
        let new_hp = apply_hp_delta(&mut s, 100).unwrap();
        assert_eq!(new_hp, 30);
        assert_eq!(s.data.player_character.hp, 30);
    }

    #[test]
    fn inventory_change_decrement_existing() {
        let mut s = make_state_with_hp(20, 30);
        s.data.player_character.inventory = vec![
            json!({"id": "potion", "name": "治疗药水", "qty": 3}),
            json!({"id": "arrow",  "name": "箭", "qty": 12}),
        ];
        let new_q = apply_inventory_change(&mut s, "arrow", -3).unwrap();
        assert_eq!(new_q, Some(9));
        assert_eq!(
            s.data.player_character.inventory[1]["qty"].as_i64(),
            Some(9),
        );
    }

    #[test]
    fn inventory_change_clamps_to_zero() {
        let mut s = make_state_with_hp(20, 30);
        s.data.player_character.inventory = vec![
            json!({"id": "torch", "qty": 1}),
        ];
        let new_q = apply_inventory_change(&mut s, "torch", -5).unwrap();
        assert_eq!(new_q, Some(0));
    }

    #[test]
    fn inventory_change_unknown_item_returns_none() {
        let mut s = make_state_with_hp(20, 30);
        s.data.player_character.inventory = vec![json!({"id": "potion", "qty": 3})];
        let r = apply_inventory_change(&mut s, "missing", -1).unwrap();
        assert_eq!(r, None);
    }

    #[test]
    fn apply_outcome_subtract_player_hp() {
        let mut s = make_state_with_hp(20, 30);
        let v_dmg = json!(7);
        let ops = vec![RulesOp {
            op: "subtract",
            path: "player_character.hp",
            value: Some(&v_dmg),
        }];
        let (applied, skipped) = apply_rules_outcome(&mut s, &ops);
        assert_eq!(skipped.len(), 0);
        assert_eq!(applied.len(), 1);
        assert_eq!(s.data.player_character.hp, 13);
    }

    #[test]
    fn apply_outcome_skips_combatant_path() {
        let mut s = make_state_with_hp(20, 30);
        let v = json!(5);
        let ops = vec![RulesOp {
            op: "subtract",
            path: "_combatant.enemy_1.hp",
            value: Some(&v),
        }];
        let (applied, skipped) = apply_rules_outcome(&mut s, &ops);
        assert_eq!(applied.len(), 0);
        assert_eq!(skipped, vec!["_combatant.enemy_1.hp".to_string()]);
        // player hp 不变
        assert_eq!(s.data.player_character.hp, 20);
    }

    #[test]
    fn apply_outcome_data_variant_works_without_gamestate() {
        let mut data: GameStateData = serde_json::from_value(default_state()).unwrap();
        data.player_character.hp = 20;
        data.player_character.max_hp = 30;
        let v_dmg = json!(7);
        let ops = vec![RulesOp {
            op: "subtract",
            path: "player_character.hp",
            value: Some(&v_dmg),
        }];
        let (applied, _) = apply_rules_outcome_data(&mut data, &ops);
        assert_eq!(applied.len(), 1);
        assert_eq!(data.player_character.hp, 13);
    }
}
