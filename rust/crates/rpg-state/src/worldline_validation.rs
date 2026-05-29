//! worldline_validation.rs — 世界线设定校验 + 推演投影写入
//!
//! 对应 Python:
//! - `rpg/state/core.py::_scan_worldline_validation`
//! - `rpg/state/core.py::_set_worldline_validation`
//! - `rpg/state/core.py::_store_worldline_projection`
//!
//! 设计:
//! - `scan_worldline_validation(state, tags)` 扫 GM 输出的 `【设定校验:】` /
//!   `【设定冲突:】` 标签,把 status 归一成 passed / conflict / review / none。
//! - `set_worldline_validation` 写 worldline.last_validation。
//! - `store_worldline_projection` 写 worldline.last_projection(或 pending_projection
//!   待用户确认)。`validated && user_variables` 关系决定走哪条分支。

use serde_json::{json, Value};
use thiserror::Error;

use crate::state::GameState;

#[derive(Debug, Error)]
pub enum WorldlineValidationError {
    #[error("state.data is not an object")]
    NotObject,
}

#[derive(Debug, Clone)]
pub struct ValidationScan {
    pub status: String,
    pub message: String,
}

/// 扫 GM 输出标签里的设定校验信号。
///
/// 对应 Python `_scan_worldline_validation(tags)`。返回 status 之一:
/// `passed` / `conflict` / `review` / `none`。
pub fn scan_worldline_validation(state: &GameState, tags: &[String]) -> ValidationScan {
    let mut status = String::from("none");
    let mut message = String::new();
    for item in tags {
        let (key, value) = split_label(item);
        if key.contains("设定冲突") {
            return ValidationScan {
                status: "conflict".to_string(),
                message: if value.is_empty() { item.clone() } else { value },
            };
        }
        if key.contains("设定校验") {
            let v = &value;
            let passed_words = ["通过", "满足", "无冲突", "ok", "OK"];
            if passed_words.iter().any(|w| v.contains(w)) {
                status = "passed".to_string();
            } else {
                status = "review".to_string();
            }
            message = value;
        }
    }
    // 有 user_variables 且 GM 提到「世界线推演」但没显式校验 → review
    let has_user_vars = !state.data.worldline.user_variables.is_empty();
    let has_projection_tag = tags.iter().any(|t| t.contains("世界线推演"));
    if has_user_vars && has_projection_tag && status == "none" {
        return ValidationScan {
            status: "review".to_string(),
            message: "推演缺少【设定校验:通过】".to_string(),
        };
    }
    ValidationScan { status, message }
}

/// 写 worldline.last_validation。
///
/// 对应 Python `_set_worldline_validation(status, message)`。
pub fn set_worldline_validation(state: &mut GameState, status: &str, message: &str) {
    use rpg_schemas::WorldlineValidation;
    let turn = state.turn();
    state.data.worldline.last_validation = WorldlineValidation {
        status: status.to_string(),
        message: message.to_string(),
        turn: turn as u64,
        extra: Default::default(),
    };
    state.touch();
}

/// 写 worldline.last_projection 或 pending_projection,看是否通过校验。
///
/// 对应 Python `_store_worldline_projection(text, validated)`。
/// 返回 true 代表写入 last_projection,false 代表落入 pending(待用户确认)。
pub fn store_worldline_projection(state: &mut GameState, text: &str, validated: bool) -> bool {
    let cleaned = clean_item(text);
    let turn = state.turn();
    let world_time = state.data.world.time.clone();
    let user_vars = Value::Object(state.data.worldline.user_variables.clone());
    let projection = json!({
        "text": cleaned,
        "turn": turn,
        "validated": validated,
        "time": world_time,
        "variables": user_vars,
    });

    let has_user_vars = !state.data.worldline.user_variables.is_empty();
    let write_last = validated || !has_user_vars;
    if write_last {
        state.data.worldline.last_projection = Some(projection);
        state.data.worldline.pending_projection = None;
        state.touch();
        true
    } else {
        state.data.worldline.pending_projection = Some(projection);
        state.touch();
        false
    }
}

/// 校验状态 → 中文标签。对应 Python `_validation_label`。
pub fn validation_label(status: &str) -> &'static str {
    match status {
        "passed" => "通过",
        "conflict" => "冲突",
        "review" => "待审",
        "none" => "无",
        _ => "未知",
    }
}

// ─────────────────────────────────────────────────────────────
// helpers (复制自 structured.rs / pending.rs, 避免跨 module 暴露 private)
// ─────────────────────────────────────────────────────────────

fn clean_item(text: &str) -> String {
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

fn split_label(text: &str) -> (String, String) {
    for sep in ["：", ":"] {
        if let Some(pos) = text.find(sep) {
            let (l, r) = text.split_at(pos);
            let right = &r[sep.len()..];
            return (clean_item(l), clean_item(right));
        }
    }
    (clean_item(text), clean_item(text))
}
