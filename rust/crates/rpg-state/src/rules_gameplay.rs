//! rules_gameplay.rs — memory items / relationships / hypothesis 三件套
//!
//! 对应 Python:
//! - `rpg/state/_mixins/rules_gameplay.py` 大部分(active_entities/dice_log
//!   等留给后续 PR — 它们和 rules_engine 强耦合,需要 rpg-rules crate 同步迁)。
//! - `rpg/state/core.py::add_memory / add_memory_item / add_hypothesis /
//!    confirm_hypothesis / reject_hypothesis / update_relationship`。
//!
//! 三个 public function:
//! - [`add_memory_item`] — 结构化 memory.items 写入(task 74)
//! - [`update_relationship`] — relationships[name] = status
//! - [`record_hypothesis`] — 写一条 kind=hypothesis 的 memory item
//!
//! 与 Python 差异:
//! - `add_memory(bucket, text)` 旧 buckets(facts/notes/pinned/abilities/
//!   resources)dual-write 路径在 Rust 侧合并成 [`add_memory_item`] +
//!   可选 `legacy_bucket` 字段,调用方自己决定要不要同时 append 老 array。
//!   这避免了 Python 侧 mixin 互调("add_memory 调 add_memory_item 调
//!   ... 调 mark_user_locked")的隐式耦合链。
//! - `confirm_hypothesis` 仍走 add_memory_item(kind=runtime_fact,
//!   supersedes=[old_id]),与 Python 等价。

use chrono::Utc;
use once_cell::sync::Lazy;
use rand::{distributions::Alphanumeric, Rng};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use thiserror::Error;

use crate::state::GameState;

#[derive(Debug, Error)]
pub enum RulesGameplayError {
    #[error("empty text")]
    EmptyText,
    #[error("memory item {0} not found")]
    HypothesisNotFound(String),
    #[error("memory item {0} is not active hypothesis")]
    NotActiveHypothesis(String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryItem {
    pub id: String,
    pub kind: String,
    pub text: String,
    pub source: String,
    pub turn: i64,
    pub ts: String,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub time_label: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub characters: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub supersedes: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub legacy_bucket: Option<String>,
}

/// memory item 写入参数。
#[derive(Debug, Clone, Default)]
pub struct AddMemoryItemArgs<'a> {
    pub text: &'a str,
    pub kind: &'a str,
    pub source: &'a str,
    pub time_label: Option<&'a str>,
    pub characters: Option<Vec<String>>,
    pub status: Option<&'a str>,
    pub supersedes: Option<Vec<String>>,
    pub legacy_bucket: Option<&'a str>,
}

static VALID_KINDS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    ["canon_fact", "runtime_fact", "hypothesis", "user_constraint"]
        .into_iter()
        .collect()
});

/// 对应 Python `clean_item`。
fn clean_text(text: &str) -> String {
    let trimmed = text
        .trim_matches(|c: char| c.is_whitespace() || matches!(c, ':' | '：' | '-' | '—'));
    let mut out = String::with_capacity(trimmed.len());
    let mut prev_ws = false;
    for c in trimmed.chars() {
        if c.is_whitespace() {
            if !prev_ws {
                out.push(' ');
                prev_ws = true;
            }
        } else {
            out.push(c);
            prev_ws = false;
        }
    }
    out.trim().to_string()
}

fn random_suffix(n: usize) -> String {
    rand::thread_rng()
        .sample_iter(&Alphanumeric)
        .take(n)
        .map(char::from)
        .collect()
}

/// 对应 Python `add_memory_item`。返回新建 item 的 id;text 为空返回 ""。
///
/// 软上限 500 条;超过自动 drain 最老。
pub fn add_memory_item(
    state: &mut GameState,
    args: AddMemoryItemArgs<'_>,
) -> Result<String, RulesGameplayError> {
    let text = clean_text(args.text);
    if text.is_empty() {
        return Err(RulesGameplayError::EmptyText);
    }
    let kind = if VALID_KINDS.contains(args.kind) {
        args.kind
    } else {
        "runtime_fact"
    };
    let status = args.status.unwrap_or("active");
    let id = format!("mem_{}", random_suffix(10));
    let turn = state.turn();
    let mut item = json!({
        "id": id,
        "kind": kind,
        "text": text,
        "source": args.source,
        "turn": turn,
        "ts": Utc::now().to_rfc3339(),
        "status": status,
    });
    if let Some(label) = args.time_label {
        if !label.is_empty() {
            item["time_label"] = Value::String(label.to_string());
        }
    }
    if let Some(chars) = args.characters {
        if !chars.is_empty() {
            item["characters"] = Value::Array(chars.into_iter().map(Value::String).collect());
        }
    }
    if let Some(sup) = args.supersedes {
        if !sup.is_empty() {
            item["supersedes"] = Value::Array(sup.into_iter().map(Value::String).collect());
        }
    }
    if let Some(bucket) = args.legacy_bucket {
        if !bucket.is_empty() {
            item["legacy_bucket"] = Value::String(bucket.to_string());
        }
    }

    let items = ensure_memory_items(state);
    items.push(item);
    let len = items.len();
    if len > 500 {
        items.drain(0..len - 500);
    }
    state.touch();
    Ok(id)
}

/// 对应 Python `update_relationship(char, status)`。`status` 为空字符串会
/// 写入空 string,与 Python 行为一致(Python 不做空值过滤)。
pub fn update_relationship(state: &mut GameState, character: &str, status: &str) {
    let name = clean_text(character);
    if name.is_empty() {
        return;
    }
    state.data.relationships.insert(name, Value::String(status.to_string()));
    state.touch();
}

/// 对应 Python `add_hypothesis` — kind=hypothesis 的 memory item。
pub fn record_hypothesis(
    state: &mut GameState,
    text: &str,
    source: &str,
    time_label: Option<&str>,
    characters: Option<Vec<String>>,
) -> Result<String, RulesGameplayError> {
    add_memory_item(
        state,
        AddMemoryItemArgs {
            text,
            kind: "hypothesis",
            source,
            time_label,
            characters,
            status: Some("active"),
            supersedes: None,
            legacy_bucket: None,
        },
    )
}

/// 对应 Python `confirm_hypothesis`:把指定 hypothesis 标 superseded,
/// 新建一条 kind=runtime_fact 引用其 id。返回新 fact 的 id。
pub fn confirm_hypothesis(
    state: &mut GameState,
    item_id: &str,
    source: &str,
) -> Result<String, RulesGameplayError> {
    let (text, time_label, characters) = {
        let items = match memory_items_ref(state) {
            Some(v) => v,
            None => return Err(RulesGameplayError::HypothesisNotFound(item_id.to_string())),
        };
        let target = items.iter().find(|i| {
            i.get("id")
                .and_then(Value::as_str)
                .map(|s| s == item_id)
                .unwrap_or(false)
        });
        let target = match target {
            Some(t) => t,
            None => return Err(RulesGameplayError::HypothesisNotFound(item_id.to_string())),
        };
        if target.get("kind").and_then(Value::as_str) != Some("hypothesis") {
            return Err(RulesGameplayError::NotActiveHypothesis(item_id.to_string()));
        }
        if target.get("status").and_then(Value::as_str) != Some("active") {
            return Err(RulesGameplayError::NotActiveHypothesis(item_id.to_string()));
        }
        let text = target
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let time_label = target
            .get("time_label")
            .and_then(Value::as_str)
            .map(|s| s.to_string());
        let characters = target
            .get("characters")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect::<Vec<String>>()
            });
        (text, time_label, characters)
    };

    // 标 superseded
    let items = ensure_memory_items(state);
    if let Some(target) = items.iter_mut().find(|i| {
        i.get("id")
            .and_then(Value::as_str)
            .map(|s| s == item_id)
            .unwrap_or(false)
    }) {
        if let Some(obj) = target.as_object_mut() {
            obj.insert(
                "status".to_string(),
                Value::String("superseded".to_string()),
            );
        }
    }

    add_memory_item(
        state,
        AddMemoryItemArgs {
            text: &text,
            kind: "runtime_fact",
            source,
            time_label: time_label.as_deref(),
            characters,
            status: Some("active"),
            supersedes: Some(vec![item_id.to_string()]),
            legacy_bucket: None,
        },
    )
}

/// 对应 Python `reject_hypothesis`:把指定 hypothesis 标 rejected。
pub fn reject_hypothesis(state: &mut GameState, item_id: &str) -> bool {
    let items = match ensure_memory_items_opt(state) {
        Some(v) => v,
        None => return false,
    };
    let Some(target) = items.iter_mut().find(|i| {
        i.get("id")
            .and_then(Value::as_str)
            .map(|s| s == item_id)
            .unwrap_or(false)
    }) else {
        return false;
    };
    if target.get("kind").and_then(Value::as_str) != Some("hypothesis") {
        return false;
    }
    if let Some(obj) = target.as_object_mut() {
        obj.insert(
            "status".to_string(),
            Value::String("rejected".to_string()),
        );
    }
    state.touch();
    true
}

// ─────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────

fn ensure_memory_items(state: &mut GameState) -> &mut Vec<Value> {
    &mut state.data.memory.items
}

fn ensure_memory_items_opt(state: &mut GameState) -> Option<&mut Vec<Value>> {
    Some(ensure_memory_items(state))
}

fn memory_items_ref(state: &GameState) -> Option<&Vec<Value>> {
    Some(&state.data.memory.items)
}
