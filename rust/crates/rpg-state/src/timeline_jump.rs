//! timeline_jump.rs — 时间跳跃状态机 (request / confirm / reject)
//!
//! 对应 Python `rpg/state/core.py` 里 `request_time_jump` /
//! `confirm_time_jump` / `reject_time_jump` + `update_time` 同时维护的
//! `world.timeline.{anchor_state, pending_jump, last_transition,
//! anchor_source, anchor_turn, current_label, current_phase}` 一整套。
//!
//! 协议(三步事务):
//! 1. `request_time_jump(target, raw)` — 玩家自然语言触发,把 timeline
//!    标 `pending_confirmation`,写 `pending_jump = {from, to, raw, turn,
//!    status: awaiting_gm_confirmation}`。
//! 2. `confirm_time_jump(target?)` — GM 同意,把 pending 的 to 落到
//!    `world.time` + 清 pending_jump + 锁回 `locked`。
//! 3. `reject_time_jump(reason)` — GM 拒绝,清 pending_jump + 写
//!    `last_transition` 留痕。
//!
//! 与 Python 差异:
//! - `clean_time_value` 简化:Python 那串 strip"(后)再?(行动|出发|继续...)$"
//!   regex 主要是为了清 LLM 输出尾巴,这里同步搬过来。
//! - `update_time(time_desc, source)` 抽出来同 module 内部用,不再涉及
//!   `_phase_for_time` 推断(那段逻辑依赖剧本知识,Python 现在也基本只在
//!   modules 里走;Rust 侧 phase 派生交给 rules_bridge 后续接管)。
//! - `_is_user_locked` 在 ops::mark_user_locked 里登记到 `player_private
//!   .user_locked_fields`,Python 用 `worldline.user_locked_fields`;Rust
//!   migration 时按 ops 现状对齐,二选一不冲突。

use chrono::Utc;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
use thiserror::Error;
use tracing::warn;

use crate::state::GameState;

#[derive(Debug, Error)]
pub enum TimelineJumpError {
    #[error("time target empty after cleaning")]
    EmptyTarget,
    #[error("no pending_jump to confirm")]
    NoPendingJump,
}

#[derive(Debug, Clone)]
pub struct TimelineJumpResult {
    /// 状态机最终 anchor_state(locked / pending_confirmation)
    pub anchor_state: String,
    /// world.time 的新值(可能与 target 相同;reject 时为旧值)
    pub world_time: String,
    /// 给上层的人类可读消息
    pub message: String,
}

// ─────────────────────────────────────────────────────────────
// 公共 API
// ─────────────────────────────────────────────────────────────

/// 玩家请求时间跳跃 — 状态机入口。
pub fn request_time_jump(
    state: &mut GameState,
    target: &str,
    raw: &str,
) -> Result<TimelineJumpResult, TimelineJumpError> {
    let cleaned = clean_time_value(target);
    if cleaned.is_empty() {
        return Err(TimelineJumpError::EmptyTarget);
    }
    let turn = state.turn();
    let world_time = current_world_time(state);
    let timeline = ensure_timeline(state);
    timeline.insert(
        "anchor_state".to_string(),
        Value::String("pending_confirmation".to_string()),
    );
    timeline.insert(
        "pending_jump".to_string(),
        json!({
            "from": world_time,
            "to": cleaned,
            "raw": raw,
            "turn": turn,
            "status": "awaiting_gm_confirmation",
        }),
    );
    state.touch();
    push_audit_jump(
        state,
        json!({
            "ts": Utc::now().to_rfc3339(),
            "kind": "time_jump_requested",
            "target": cleaned,
            "raw": raw,
            "turn": turn,
        }),
    );
    Ok(TimelineJumpResult {
        anchor_state: "pending_confirmation".to_string(),
        world_time: current_world_time(state),
        message: format!("时间跳跃待确认:{cleaned}"),
    })
}

/// GM 确认时间跳跃。target 为空时取 pending_jump.to。
pub fn confirm_time_jump(
    state: &mut GameState,
    target: Option<&str>,
) -> Result<TimelineJumpResult, TimelineJumpError> {
    let pending_to = pending_jump_to(state);
    let candidate = target.map(|s| s.to_string()).unwrap_or_else(|| {
        pending_to
            .clone()
            .unwrap_or_else(|| current_world_time(state))
    });
    let cleaned = clean_time_value(&candidate);
    if cleaned.is_empty() {
        return Err(TimelineJumpError::EmptyTarget);
    }
    let prev_time = current_world_time(state);
    update_time(state, &cleaned, "gm_confirmed");
    let turn = state.turn();
    push_audit_jump(
        state,
        json!({
            "ts": Utc::now().to_rfc3339(),
            "kind": "time_jump_confirmed",
            "from": prev_time,
            "to": cleaned,
            "turn": turn,
        }),
    );
    Ok(TimelineJumpResult {
        anchor_state: "locked".to_string(),
        world_time: cleaned.clone(),
        message: format!("时间跳跃确认:{cleaned}"),
    })
}

/// GM 拒绝时间跳跃。
pub fn reject_time_jump(state: &mut GameState, reason: &str) -> TimelineJumpResult {
    let turn = state.turn();
    let (from, to) = {
        let pending = pending_jump_object(state);
        let from = pending
            .as_ref()
            .and_then(|p| p.get("from"))
            .and_then(Value::as_str)
            .map(|s| s.to_string())
            .unwrap_or_else(|| current_world_time(state));
        let to = pending
            .as_ref()
            .and_then(|p| p.get("to"))
            .and_then(Value::as_str)
            .map(|s| s.to_string())
            .unwrap_or_default();
        (from, to)
    };
    let timeline = ensure_timeline(state);
    timeline.insert(
        "last_transition".to_string(),
        json!({
            "from": from,
            "to": to,
            "source": "gm_rejected",
            "reason": reason,
            "turn": turn,
        }),
    );
    timeline.insert(
        "anchor_state".to_string(),
        Value::String("locked".to_string()),
    );
    timeline.insert("pending_jump".to_string(), Value::Null);
    state.touch();
    push_audit_jump(
        state,
        json!({
            "ts": Utc::now().to_rfc3339(),
            "kind": "time_jump_rejected",
            "to": to,
            "reason": reason,
            "turn": turn,
        }),
    );
    TimelineJumpResult {
        anchor_state: "locked".to_string(),
        world_time: current_world_time(state),
        message: format!("时间跳跃拒绝:{reason}"),
    }
}

// ─────────────────────────────────────────────────────────────
// 内部 helpers
// ─────────────────────────────────────────────────────────────

/// 直接 update world.time 同步 timeline 锚点。对应 Python `update_time`。
/// 不做 phase 派生(留给后续 rules_bridge)。
pub(crate) fn update_time(state: &mut GameState, time_desc: &str, source: &str) {
    let cleaned = clean_time_value(time_desc);
    if cleaned.is_empty() {
        return;
    }
    let turn = state.turn();
    let old_label = state
        .data
        .get("world")
        .and_then(|w| w.get("timeline"))
        .and_then(|t| t.get("current_label"))
        .and_then(Value::as_str)
        .map(|s| s.to_string());
    // world.time 直接写
    if let Some(world) = state
        .data
        .as_object_mut()
        .and_then(|m| m.get_mut("world"))
        .and_then(Value::as_object_mut)
    {
        world.insert("time".to_string(), Value::String(cleaned.clone()));
    } else {
        // world 不存在或不是 object — 兜底建一个
        if let Some(root) = state.data.as_object_mut() {
            root.insert(
                "world".to_string(),
                json!({ "time": cleaned, "timeline": {}, "known_events": [] }),
            );
        }
    }
    let timeline = ensure_timeline(state);
    timeline.insert(
        "current_label".to_string(),
        Value::String(cleaned.clone()),
    );
    timeline.insert(
        "anchor_state".to_string(),
        Value::String("locked".to_string()),
    );
    timeline.insert(
        "anchor_source".to_string(),
        Value::String(source.to_string()),
    );
    timeline.insert("anchor_turn".to_string(), json!(turn));
    timeline.insert(
        "last_transition".to_string(),
        json!({
            "from": old_label,
            "to": cleaned,
            "source": source,
            "turn": turn,
        }),
    );
    timeline.insert("pending_jump".to_string(), Value::Null);
    if source == "user_set" {
        timeline.insert("user_set_jump_turn".to_string(), json!(turn));
    }
    state.touch();
}

fn pending_jump_to(state: &GameState) -> Option<String> {
    state
        .data
        .get("world")
        .and_then(|w| w.get("timeline"))
        .and_then(|t| t.get("pending_jump"))
        .and_then(|p| p.get("to"))
        .and_then(Value::as_str)
        .map(|s| s.to_string())
}

fn pending_jump_object(state: &GameState) -> Option<Value> {
    state
        .data
        .get("world")
        .and_then(|w| w.get("timeline"))
        .and_then(|t| t.get("pending_jump"))
        .cloned()
}

fn current_world_time(state: &GameState) -> String {
    state
        .data
        .get("world")
        .and_then(|w| w.get("time"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// 拿到 timeline object 的可变引用,缺失字段全 default 补齐。
fn ensure_timeline(state: &mut GameState) -> &mut serde_json::Map<String, Value> {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data is object");
    if !root.get("world").map(Value::is_object).unwrap_or(false) {
        root.insert("world".to_string(), Value::Object(serde_json::Map::new()));
    }
    let world = root
        .get_mut("world")
        .and_then(Value::as_object_mut)
        .expect("world object");
    if !world.get("timeline").map(Value::is_object).unwrap_or(false) {
        world.insert("timeline".to_string(), Value::Object(serde_json::Map::new()));
    }
    let timeline = world
        .get_mut("timeline")
        .and_then(Value::as_object_mut)
        .expect("timeline object");
    timeline.entry("anchor_state").or_insert_with(|| Value::String("locked".to_string()));
    timeline.entry("current_label").or_insert_with(|| Value::String(String::new()));
    timeline.entry("current_phase").or_insert_with(|| Value::String(String::new()));
    timeline.entry("anchor_source").or_insert_with(|| Value::String("legacy".to_string()));
    timeline.entry("anchor_turn").or_insert_with(|| json!(0));
    timeline.entry("pending_jump").or_insert(Value::Null);
    timeline.entry("last_transition").or_insert(Value::Null);
    timeline
}

/// 环形 audit log — 同 ops::push_audit,这里独立写一份避免 cross-module
/// 借用冲突。容量 200。
fn push_audit_jump(state: &mut GameState, entry: Value) {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data is object");
    if !root
        .get("permissions")
        .map(Value::is_object)
        .unwrap_or(false)
    {
        root.insert("permissions".to_string(), Value::Object(serde_json::Map::new()));
    }
    let permissions = root
        .get_mut("permissions")
        .and_then(Value::as_object_mut)
        .expect("permissions object");
    let log = permissions
        .entry("audit_log".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Value::Array(arr) = log {
        arr.push(entry);
        let len = arr.len();
        if len > 200 {
            arr.drain(0..len - 200);
        }
    } else {
        warn!(target: "rpg_state::timeline_jump", "audit_log not array; resetting");
        *log = Value::Array(Vec::new());
    }
}

// ─────────────────────────────────────────────────────────────
// time-value 清洗 (对应 Python `clean_time_value`)
// ─────────────────────────────────────────────────────────────

static LEADING_PREP: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?:到|至|在)\s*").expect("leading prep regex")
});
static TRAILING_VERB: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:后?再)?(?:行动|出发|继续|调查|处理|会合|潜入|开场|开始)$")
        .expect("trailing verb regex")
});
static WHITESPACE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\s+").expect("whitespace regex"));

/// 对应 Python `clean_time_value`:
/// 1. trim 空白 / `: ：- —`
/// 2. 折叠中间 whitespace 为单空格
/// 3. 剥头部"到 / 至 / 在"
/// 4. 剥尾部 "(后再)?(行动|出发|继续...)"
/// 5. 再做一次 trim
pub fn clean_time_value(text: &str) -> String {
    let trimmed = text
        .trim_matches(|c: char| c.is_whitespace() || matches!(c, ':' | '：' | '-' | '—'));
    let collapsed = WHITESPACE.replace_all(trimmed, " ").to_string();
    let head_stripped = LEADING_PREP.replace(&collapsed, "").to_string();
    let tail_stripped = TRAILING_VERB.replace(&head_stripped, "").to_string();
    tail_stripped
        .trim_matches(|c: char| c.is_whitespace() || matches!(c, ':' | '：' | '-' | '—'))
        .to_string()
}

/// 判断字符串看起来像时间值。对应 Python `looks_like_time_value`。
pub fn looks_like_time_value(value: &str) -> bool {
    let len = value.chars().count();
    if !(2..=80).contains(&len) {
        return false;
    }
    static TOKEN_RE: Lazy<Regex> = Lazy::new(|| {
        Regex::new(r"日|天|夜|晨|早|午|晚|周|月|年|后|前|翌|次|清晨|傍晚|深夜|黎明|柏林|图卢兹|基地|第\s*\d{1,5}\s*章")
            .expect("time token regex")
    });
    TOKEN_RE.is_match(value)
}

/// 对应 Python `is_time_key`:GM 结构化标签 key 是否在说时间。
pub fn is_time_key(key: &str) -> bool {
    const MARKERS: &[&str] = &[
        "当前时间线",
        "时间线",
        "当前时间",
        "时间跳转",
        "时间推进",
        "跳转时间",
        "时点",
    ];
    MARKERS.iter().any(|m| key.contains(m))
}
