//! combat_state.rs — RulesEngine 写入入口:active_entities / dice_log / encounter
//!
//! 对应 Python: `rpg/state/_mixins/rules_gameplay.py` 战斗 / 实体 / 场景管理片段。
//!
//! 设计:
//! - 仅暴露 RulesEngine 调用的状态写入函数。所有数值真相源(HP/AC/inventory)
//!   走 [`crate::ops::apply_op`] + `source="rules_engine"`,这里的入口走 GameState
//!   直接 set,绕过权限闸门 — 因为黑白名单已经把这几个 path 标成 rules_managed,
//!   只有 source 前缀是 `rules_engine` 时才能写,而本 module 函数就是给 rules_engine 用。
//! - `update_active_entities` 复刻 Python `set_active_entities` 的覆盖写入;
//!   `upsert_active_entity` / `prune_active_entities` 提供精细化操作。
//! - `append_dice_log` 复刻 Python 的容量截断(默认 cap=50)。
//! - `update_encounter` 整对象覆盖 encounter 字段(对应 `set_encounter`)。

use serde_json::{json, Value};
use thiserror::Error;

use crate::state::GameState;

#[derive(Debug, Error)]
pub enum CombatStateError {
    #[error("entity missing required id")]
    MissingEntityId,
}

/// 覆盖整个 `active_entities` 列表。RulesEngine / rules_bridge 专用。
///
/// 对应 Python `set_active_entities`。
pub fn update_active_entities(state: &mut GameState, entities: Vec<Value>) {
    let arr = ensure_active_entities(state);
    *arr = entities;
    state.touch();
}

/// 按 id upsert 单个 active entity。
///
/// 对应 Python `upsert_active_entity`。已存在 id 命中就合并字段,保留 source /
/// first_seen_turn,刷新 last_seen_turn。新条目补默认值。
pub fn upsert_active_entity(state: &mut GameState, entity: Value) -> Result<(), CombatStateError> {
    let Some(obj) = entity.as_object() else {
        return Err(CombatStateError::MissingEntityId);
    };
    let id = obj
        .get("id")
        .and_then(Value::as_str)
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if id.is_empty() {
        return Err(CombatStateError::MissingEntityId);
    }
    let turn = state.turn();
    let arr = ensure_active_entities(state);
    // 找已有
    if let Some(idx) = arr.iter().position(|e| {
        e.get("id")
            .and_then(Value::as_str)
            .map(|s| s == id)
            .unwrap_or(false)
    }) {
        let existing = arr[idx].clone();
        let mut merged = existing.as_object().cloned().unwrap_or_default();
        let preserved_source = merged
            .get("source")
            .cloned()
            .or_else(|| obj.get("source").cloned())
            .unwrap_or_else(|| Value::String("unknown".to_string()));
        let preserved_first_seen = merged
            .get("first_seen_turn")
            .cloned()
            .unwrap_or_else(|| Value::from(turn));
        for (k, v) in obj.iter() {
            if !v.is_null() {
                merged.insert(k.clone(), v.clone());
            }
        }
        merged.insert("source".to_string(), preserved_source);
        merged.insert("first_seen_turn".to_string(), preserved_first_seen);
        merged.insert("last_seen_turn".to_string(), Value::from(turn));
        arr[idx] = Value::Object(merged);
    } else {
        let mut new_entity = obj.clone();
        new_entity
            .entry("source".to_string())
            .or_insert_with(|| Value::String("unknown".to_string()));
        new_entity
            .entry("first_seen_turn".to_string())
            .or_insert(Value::from(turn));
        new_entity.insert("last_seen_turn".to_string(), Value::from(turn));
        new_entity
            .entry("kind".to_string())
            .or_insert_with(|| Value::String("unknown".to_string()));
        new_entity
            .entry("disposition".to_string())
            .or_insert_with(|| Value::String("unknown".to_string()));
        new_entity
            .entry("confidence".to_string())
            .or_insert_with(|| json!(1.0));
        arr.push(Value::Object(new_entity));
    }
    state.touch();
    Ok(())
}

/// 删除不在 keep_ids 里的 active entities。返回删了几个。
///
/// 对应 Python `prune_active_entities`。
pub fn prune_active_entities(state: &mut GameState, keep_ids: &[String]) -> usize {
    let arr = ensure_active_entities(state);
    let before = arr.len();
    arr.retain(|e| {
        let id = e
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        keep_ids.iter().any(|k| k == &id)
    });
    let removed = before - arr.len();
    if removed > 0 {
        state.touch();
    }
    removed
}

/// 追加一条 dice_log 条目,容量截断到 cap(默认 50)。
///
/// 对应 Python `append_dice_log(entry, cap=50)`。
pub fn append_dice_log(state: &mut GameState, entry: Value, cap: Option<usize>) {
    let cap = cap.unwrap_or(50).max(1);
    let log = ensure_dice_log(state);
    log.push(entry);
    let len = log.len();
    if len > cap {
        log.drain(0..len - cap);
    }
    state.touch();
}

/// 整对象覆盖 encounter。RulesEngine 专用。
///
/// 对应 Python `set_encounter(encounter)`。空 dict / null 等同清空。
pub fn update_encounter(state: &mut GameState, encounter: Value) {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data is object");
    let to_write = match encounter {
        Value::Object(_) => encounter,
        Value::Null => Value::Object(serde_json::Map::new()),
        _ => Value::Object(serde_json::Map::new()),
    };
    root.insert("encounter".to_string(), to_write);
    state.touch();
}

/// 清空 encounter — 对应 Python `clear_encounter` 复刻 DEFAULT_STATE 的 encounter 模板。
pub fn clear_encounter(state: &mut GameState) {
    let default = json!({
        "active": false,
        "round": 0,
        "turn_index": 0,
        "initiative_order": [],
        "combatants": [],
        "encounter_id": "",
        "log": [],
    });
    update_encounter(state, default);
}

// ─────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────

fn ensure_active_entities(state: &mut GameState) -> &mut Vec<Value> {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data is object");
    if !root
        .get("active_entities")
        .map(Value::is_array)
        .unwrap_or(false)
    {
        root.insert("active_entities".to_string(), Value::Array(Vec::new()));
    }
    root.get_mut("active_entities")
        .and_then(Value::as_array_mut)
        .expect("active_entities array")
}

fn ensure_dice_log(state: &mut GameState) -> &mut Vec<Value> {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data is object");
    if !root.get("dice_log").map(Value::is_array).unwrap_or(false) {
        root.insert("dice_log".to_string(), Value::Array(Vec::new()));
    }
    root.get_mut("dice_log")
        .and_then(Value::as_array_mut)
        .expect("dice_log array")
}
