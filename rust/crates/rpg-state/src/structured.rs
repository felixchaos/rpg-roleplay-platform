//! structured.rs — GM 输出结构化抽取与应用
//!
//! 对应 Python: `rpg/state/_mixins/apply_ops.py::apply_structured_updates`
//! + `rpg/state/json_ops.py::_extract_json_state_ops`。
//!
//! 双协议:
//! 1. ```json``` / ```state-ops``` / ```state``` 代码块 — 现代 LLM 首选,
//!    每条 op 是 `{"op": "set" | "append" | "overwrite" | "question" |
//!    "hypothesis" | "confirm_hypothesis" | "reject_hypothesis", "path":
//!    "...", "value": ...}`。
//! 2. 【…】 中文标签 — 向后兼容。剥离 JSON 块后再扫,避免双重计算。
//!
//! 与 Python 差异:
//! - 不再调 `_gm_write_via_gate` 内部 path 路由(memory.facts / abilities /
//!   resources 全套中文标签别名),Python 那段 200 行 if/elif 链与 LLM 输出
//!   习惯高度耦合,等 rpg-context 渲染层完整成型后单独迁。
//!   当前 Rust 版本只迁:
//!     · 时间标签(world.time / pending_jump)
//!     · 关系标签(relationships.{name})
//!     · 位置标签(player.current_location)
//!     · 主线/目标(memory.main_quest / memory.current_objective)
//!     · 询问玩家(add_pending_question)
//!     · JSON op:set/append/overwrite/question/hypothesis/confirm/reject
//! - Regex fallback("重力控制 / 特殊小队")是 Python 早期硬编码,Rust 不迁,
//!   等任务系统驱动起来再补 hook。
//! - 不调 `_scan_worldline_validation` —— 那段 worldline.last_validation 写入
//!   留给后续 worldline 子模块。

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::Value;
use thiserror::Error;

use crate::ops::{self, ApplyKind, Op, OpError};
use crate::pending::{add_pending_question, PendingError};
use crate::rules_gameplay::{
    confirm_hypothesis, record_hypothesis, reject_hypothesis, update_relationship,
    RulesGameplayError,
};
use crate::state::GameState;
use crate::timeline_jump::{
    confirm_time_jump, is_time_key, reject_time_jump, TimelineJumpError,
};

#[derive(Debug, Error)]
pub enum StructuredError {
    #[error("op error: {0}")]
    Op(#[from] OpError),
    #[error("pending error: {0}")]
    Pending(#[from] PendingError),
    #[error("timeline error: {0}")]
    Timeline(#[from] TimelineJumpError),
    #[error("rules_gameplay error: {0}")]
    Rules(#[from] RulesGameplayError),
}

#[derive(Debug, Clone, Default)]
pub struct UpdateResult {
    pub updates: Vec<String>,
}

/// 顶层入口 — 处理 GM 输出全套结构化标签。
///
/// 对应 Python `apply_structured_updates(gm_response, skip_regex_fallback=...)`。
pub fn apply_structured_updates(
    state: &mut GameState,
    gm_text: &str,
) -> Result<UpdateResult, StructuredError> {
    let mut result = UpdateResult::default();
    if gm_text.is_empty() {
        return Ok(result);
    }

    // 1) 剥 JSON 块
    let (json_ops, stripped_text) = extract_json_state_ops(gm_text);

    // 2) 抽 【...】 tags(从 stripped_text 里抽,避免双重计算)
    let tags = extract_brace_tags(&stripped_text);

    // 3) pending_jump 询问语境探测 — Python 的 task 22 / 35 双重防御
    let pending_jump_object = state
        .data
        .get("world")
        .and_then(|w| w.get("timeline"))
        .and_then(|t| t.get("pending_jump"))
        .cloned();
    let pending_jump_present = pending_jump_object
        .as_ref()
        .map(|v| !v.is_null())
        .unwrap_or(false);
    let asking_for_confirm =
        pending_jump_present && gm_is_asking_for_time_confirm(gm_text, &tags);

    // 4) 处理 【】 tags
    for tag in &tags {
        let (key, value) = split_label(tag);
        if key.is_empty() && value.is_empty() {
            continue;
        }
        // 位置
        if key.contains("当前位置") || key == "地点" || key == "位置" {
            apply_gm_write(state, "player.current_location", &value, &mut result, false)?;
            continue;
        }
        // 时间
        if is_time_key(&key) {
            if pending_jump_present && asking_for_confirm {
                result
                    .updates
                    .push(format!("时间提案保留待确认:{value}"));
                continue;
            }
            apply_gm_write(state, "world.time", &value, &mut result, false)?;
            continue;
        }
        // 时间跳跃确认
        if key.contains("时间跳跃确认") {
            let v_lower = value.to_lowercase();
            let value_pending = ["待确认", "未确认", "暂不", "暂缓"]
                .iter()
                .any(|m| value.contains(m))
                || ["pending", "awaiting"].iter().any(|m| v_lower.contains(m));
            if pending_jump_present && (asking_for_confirm || value_pending) {
                let label = if value.is_empty() { &key } else { &value };
                result
                    .updates
                    .push(format!("时间跳跃确认保留待确认:{label}"));
                continue;
            }
            let target = if value.is_empty() { Some(key.as_str()) } else { Some(value.as_str()) };
            match confirm_time_jump(state, target) {
                Ok(res) => result.updates.push(res.message),
                Err(e) => result.updates.push(format!("时间跳跃确认失败:{e}")),
            }
            continue;
        }
        // 时间跳跃拒绝
        if key.contains("时间跳跃拒绝") {
            let res = reject_time_jump(state, &value);
            result.updates.push(res.message);
            continue;
        }
        // 询问玩家
        if key.contains("询问玩家") || key.contains("向玩家提问") || key.contains("澄清问题") {
            let q_text = if value.is_empty() { &key } else { &value };
            if add_pending_question(state, q_text, "gm", None) {
                result.updates.push("等待玩家回答".to_string());
            }
            continue;
        }
        // 关系
        if key.contains("关系") {
            if let Some((name, status)) = split_relation(&value) {
                update_relationship(state, &name, &status);
                result
                    .updates
                    .push(format!("关系:{name} -> {status}"));
                continue;
            }
        }
        // 目标
        if key.contains("当前目标") || key == "目标" {
            apply_gm_write(state, "memory.current_objective", &value, &mut result, false)?;
            continue;
        }
        // 主线
        if key.contains("主线任务更新") || key.contains("主线") {
            apply_gm_write(state, "memory.main_quest", &value, &mut result, false)?;
            apply_gm_write(state, "memory.current_objective", &value, &mut result, false)?;
            continue;
        }
        // 默认:不识别的 key 当 facts append
        if !tag.trim().is_empty() {
            apply_gm_write(state, "memory.facts", tag, &mut result, true)?;
        }
    }

    // 5) 处理 JSON ops
    for op in &json_ops {
        process_json_op(state, op, &mut result)?;
    }

    // 6) memory.last_structured_updates
    write_last_structured_updates(state, &result.updates);

    Ok(result)
}

// ─────────────────────────────────────────────────────────────
// JSON op 协议
// ─────────────────────────────────────────────────────────────

fn process_json_op(
    state: &mut GameState,
    op: &Value,
    result: &mut UpdateResult,
) -> Result<(), StructuredError> {
    let Some(op_obj) = op.as_object() else {
        return Ok(());
    };
    let kind = op_obj
        .get("op")
        .and_then(Value::as_str)
        .unwrap_or("set")
        .to_lowercase();

    if kind == "question" {
        let q = op_obj
            .get("question")
            .and_then(Value::as_str)
            .or_else(|| op_obj.get("text").and_then(Value::as_str))
            .unwrap_or("")
            .to_string();
        if q.is_empty() {
            log_op_parse_error(state, "question op 缺 'question' 或 'text' 字段");
            result
                .updates
                .push(format!("JSON op 忽略(询问缺文本):{op}"));
            return Ok(());
        }
        let options = op_obj
            .get("options")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(|s| s.to_string()))
                    .collect::<Vec<String>>()
            });
        if add_pending_question(state, &q, "gm:json", options) {
            result.updates.push("等待玩家回答".to_string());
        }
        return Ok(());
    }

    if kind == "hypothesis" {
        let text = op_obj
            .get("text")
            .and_then(Value::as_str)
            .or_else(|| op_obj.get("value").and_then(Value::as_str))
            .unwrap_or("");
        if text.is_empty() {
            log_op_parse_error(state, "hypothesis op 缺 'text' 或 'value' 字段");
            result
                .updates
                .push(format!("JSON op 忽略(推测缺文本):{op}"));
            return Ok(());
        }
        let time_label = op_obj.get("time_label").and_then(Value::as_str);
        let characters = op_obj.get("characters").and_then(Value::as_array).map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect::<Vec<String>>()
        });
        let mid = record_hypothesis(state, text, "gm:json", time_label, characters)?;
        let snippet: String = text.chars().take(40).collect();
        result
            .updates
            .push(format!("推测登记:{mid} {snippet}"));
        return Ok(());
    }

    if kind == "confirm_hypothesis" {
        let hid = op_obj.get("id").and_then(Value::as_str).unwrap_or("");
        if hid.is_empty() {
            result.updates.push("推测确认失败(无 id)".to_string());
            return Ok(());
        }
        match confirm_hypothesis(state, hid, "gm:json") {
            Ok(_) => result.updates.push(format!("推测确认:{hid}")),
            Err(e) => result
                .updates
                .push(format!("推测确认失败({hid}):{e}")),
        }
        return Ok(());
    }

    if kind == "reject_hypothesis" {
        let hid = op_obj.get("id").and_then(Value::as_str).unwrap_or("");
        if hid.is_empty() {
            result.updates.push("推测拒绝失败(无 id)".to_string());
            return Ok(());
        }
        if reject_hypothesis(state, hid) {
            result.updates.push(format!("推测拒绝:{hid}"));
        } else {
            result
                .updates
                .push(format!("推测拒绝失败(id 不存在):{hid}"));
        }
        return Ok(());
    }

    // set / append / overwrite
    let path = op_obj
        .get("path")
        .and_then(Value::as_str)
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if path.is_empty() {
        log_op_parse_error(state, "set/append op 缺 'path' 字段");
        result
            .updates
            .push(format!("JSON op 忽略(缺 path):{op}"));
        return Ok(());
    }
    let value = op_obj.get("value").cloned().unwrap_or(Value::Null);
    let append = kind == "append";
    let typed_op = if append {
        Op::Append { path: path.clone(), value }
    } else {
        Op::Set { path: path.clone(), value }
    };
    let snippet = format!("{kind}: {path}");
    match ops::apply_op(state, typed_op, "gm", false) {
        Ok(outcome) => match outcome.kind {
            ApplyKind::Applied => result.updates.push(snippet),
            ApplyKind::Pending => result.updates.push(format!("状态写入待审:{path}")),
            ApplyKind::Rejected => result.updates.push(format!("状态写入拒绝:{path}")),
        },
        Err(e) => result.updates.push(format!("JSON op 失败:{e}")),
    }
    Ok(())
}

/// 走 ops::apply_op 的 GM 写入。append=true 时构造 [`Op::Append`],否则 [`Op::Set`]。
///
/// 与 Python `_gm_write_via_gate` 简化版对齐:成功 → 用 "状态写入:path";
/// pending/reject → 用 ops 返回的原始 message。不实现 Python 的友好文案
/// label_for_update,Rust 侧调用方自己拼。
fn apply_gm_write(
    state: &mut GameState,
    path: &str,
    value: &str,
    result: &mut UpdateResult,
    append: bool,
) -> Result<(), StructuredError> {
    let v = Value::String(value.to_string());
    let op = if append {
        Op::Append {
            path: path.to_string(),
            value: v,
        }
    } else {
        Op::Set {
            path: path.to_string(),
            value: v,
        }
    };
    match ops::apply_op(state, op, "gm", false) {
        Ok(outcome) => match outcome.kind {
            ApplyKind::Applied => result.updates.push(outcome.message),
            ApplyKind::Pending => result.updates.push(outcome.message),
            ApplyKind::Rejected => result.updates.push(outcome.message),
        },
        Err(e) => result.updates.push(format!("状态写入失败({path}):{e}")),
    }
    Ok(())
}

// ─────────────────────────────────────────────────────────────
// 提取器
// ─────────────────────────────────────────────────────────────

static JSON_OPS_FENCE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?ms)```(?:json|state-ops|state)?\s*\n?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*\n?```",
    )
    .expect("json fence regex")
});

/// 对应 Python `_extract_json_state_ops`。返回 (ops_list, stripped_text)。
pub fn extract_json_state_ops(text: &str) -> (Vec<Value>, String) {
    if text.is_empty() || !text.contains("```") {
        return (Vec::new(), text.to_string());
    }
    let mut ops: Vec<Value> = Vec::new();
    let mut stripped_parts: Vec<String> = Vec::new();
    let mut last_end = 0;
    for caps in JSON_OPS_FENCE_RE.captures_iter(text) {
        let m = caps.get(0).expect("whole match");
        stripped_parts.push(text[last_end..m.start()].to_string());
        last_end = m.end();
        let raw = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        match serde_json::from_str::<Value>(raw) {
            Ok(parsed) => match parsed {
                Value::Object(_) => {
                    // 启发式:必须看着像 state op
                    if has_op_key(&parsed) {
                        ops.push(parsed);
                    } else {
                        stripped_parts.push(m.as_str().to_string());
                    }
                }
                Value::Array(arr) => {
                    for item in arr {
                        if item.is_object() && has_op_key(&item) {
                            ops.push(item);
                        }
                    }
                }
                _ => {
                    stripped_parts.push(m.as_str().to_string());
                }
            },
            Err(_) => {
                // 保留原 fence,让玩家看到解析失败
                stripped_parts.push(m.as_str().to_string());
            }
        }
    }
    stripped_parts.push(text[last_end..].to_string());
    (ops, stripped_parts.concat())
}

fn has_op_key(v: &Value) -> bool {
    let Some(obj) = v.as_object() else {
        return false;
    };
    obj.contains_key("op") || obj.contains_key("path") || obj.contains_key("question")
}

static BRACE_TAG_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"【([^】]+)】").expect("brace tag regex")
});

/// 从文本里抽 【...】 内容。同 Python apply_structured_updates 抽 tags 段。
fn extract_brace_tags(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for caps in BRACE_TAG_RE.captures_iter(text) {
        let whole = caps.get(0).expect("whole match");
        let inner = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        // Markdown option labels:"- **【搜寻车厢】** ..." 是 UI 选项,不是事实
        let start_byte = whole.start();
        let line_start_byte = text[..start_byte].rfind('\n').map(|i| i + 1).unwrap_or(0);
        let end_byte = whole.end();
        let line_end_byte = text[end_byte..]
            .find('\n')
            .map(|i| end_byte + i)
            .unwrap_or(text.len());
        let line = &text[line_start_byte..line_end_byte];
        if line.contains("**【") && line.contains("】**") {
            let trimmed_line = line.trim_start();
            if line.contains(" - ") || trimmed_line.starts_with('-') {
                continue;
            }
        }
        let cleaned = clean_item(inner);
        if !cleaned.is_empty() {
            out.push(cleaned);
        }
    }
    out
}

/// 对应 Python `_gm_is_asking_for_time_confirm` 的简化版:
/// 在 GM 正文或 tags 里找询问/待确认信号。
fn gm_is_asking_for_time_confirm(text: &str, tags: &[String]) -> bool {
    const SIGNALS: &[&str] = &[
        "待确认",
        "请确认",
        "是否",
        "询问玩家",
        "awaiting",
        "pending",
        "等待玩家",
        "设定冲突",
    ];
    let low = text.to_lowercase();
    if SIGNALS.iter().any(|s| text.contains(s) || low.contains(&s.to_lowercase())) {
        return true;
    }
    for tag in tags {
        if SIGNALS.iter().any(|s| tag.contains(s)) {
            return true;
        }
    }
    false
}

// ─────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────

fn write_last_structured_updates(state: &mut GameState, updates: &[String]) {
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data object");
    if !root.get("memory").map(Value::is_object).unwrap_or(false) {
        root.insert("memory".to_string(), Value::Object(serde_json::Map::new()));
    }
    if let Some(memory) = root.get_mut("memory").and_then(Value::as_object_mut) {
        let tail: Vec<Value> = updates
            .iter()
            .rev()
            .take(12)
            .rev()
            .map(|s| Value::String(s.clone()))
            .collect();
        memory.insert("last_structured_updates".to_string(), Value::Array(tail));
    }
}

fn log_op_parse_error(state: &mut GameState, hint: &str) {
    use chrono::Utc;
    use serde_json::json;
    if !state.data.is_object() {
        state.data = Value::Object(serde_json::Map::new());
    }
    let root = state.data.as_object_mut().expect("state.data object");
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
    let audit = permissions
        .entry("audit_log".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Value::Array(arr) = audit {
        arr.push(json!({
            "ts": Utc::now().to_rfc3339(),
            "kind": "parse_error",
            "source": "gm:json",
            "hint": hint,
        }));
        let len = arr.len();
        if len > 200 {
            arr.drain(0..len - 200);
        }
    }
}

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

fn split_relation(text: &str) -> Option<(String, String)> {
    for sep in ["：", ":", "->", "→", "-"] {
        if let Some(pos) = text.find(sep) {
            let (l, r) = text.split_at(pos);
            let right = &r[sep.len()..];
            let left = clean_item(l);
            let right = clean_item(right);
            if !left.is_empty() && !right.is_empty() {
                return Some((left, right));
            }
        }
    }
    None
}

