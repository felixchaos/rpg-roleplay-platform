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
//! - 全套 GM 中文标签 dispatch(W3-2 补完):
//!   · 时间标签(world.time / pending_jump)+ 时间跳跃确认/拒绝
//!   · 关系标签(relationships.{name})
//!   · 位置标签(player.current_location)
//!   · 主线/目标(memory.main_quest / memory.current_objective)
//!   · 询问玩家(add_pending_question)
//!   · 资源(memory.resources,_split_items 切分)
//!   · 能力/技能/掌握(memory.abilities)
//!   · 用户变量(worldline.user_variables.{key})
//!   · 状态写入 / 追加 / 覆盖(走 ops::apply_op)
//!   · 设定校验 / 设定冲突 → worldline.last_validation
//!   · 世界线推演 / 推演结果 → worldline.last_projection
//!   · 获得新身份 / 身份 → memory.facts append
//!   · JSON op:set/append/overwrite/question/hypothesis/confirm/reject
//! - Regex fallback("重力控制 / 特殊小队")是 Python 早期硬编码,Rust 不迁,
//!   等任务系统驱动起来再补 hook。

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
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
use crate::worldline_validation::{
    scan_worldline_validation, set_worldline_validation, store_worldline_projection,
    validation_label,
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

    // 2.1) 世界线设定校验 — 扫 tags 里的 设定校验 / 设定冲突 → worldline.last_validation
    let validation = scan_worldline_validation(state, &tags);
    if validation.status != "none" {
        set_worldline_validation(state, &validation.status, &validation.message);
        result.updates.push(format!(
            "设定校验:{}",
            validation_label(&validation.status)
        ));
    }
    let validated = validation.status == "passed";

    // 3) pending_jump 询问语境探测 — Python 的 task 22 / 35 双重防御
    let pending_jump_object = state.data.world.timeline.pending_jump.clone();
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

        // 设定校验 / 设定冲突 — 已在 scan 阶段处理,这里跳过避免落到 default 写 facts
        if key.contains("设定校验") || key.contains("设定冲突") {
            continue;
        }

        // 世界线推演 / 推演结果
        if key.contains("世界线推演")
            || key.contains("世界线预测")
            || key.contains("推演结果")
        {
            let stored = store_worldline_projection(state, &value, validated);
            if stored {
                result.updates.push("世界线推演:已写回".to_string());
            } else {
                result.updates.push("世界线推演:待用户确认".to_string());
            }
            continue;
        }

        // 用户变量(GM 主动设定/调整)
        if key.contains("用户变量")
            || key == "变量"
            || key == "设定变量"
            || key == "玩家变量"
        {
            let (var_key, var_value) = parse_assignment(&value);
            if !var_key.is_empty() && set_user_variable(state, &var_key, &var_value, "gm") {
                result
                    .updates
                    .push(format!("用户变量:{var_key}={var_value}"));
            }
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

        // 状态写入 / UI变量 / 界面变量
        if key.contains("状态写入") || key.contains("UI变量") || key.contains("界面变量") {
            let (path, raw_value) = parse_assignment(&value);
            if !path.is_empty() {
                apply_gm_write(state, &path, &raw_value, &mut result, false)?;
            }
            continue;
        }
        // 状态追加 / 追加变量
        if key.contains("状态追加") || key.contains("追加变量") {
            let (path, raw_value) = parse_assignment(&value);
            if !path.is_empty() {
                apply_gm_write(state, &path, &raw_value, &mut result, true)?;
            }
            continue;
        }
        // 状态覆盖 / 覆盖变量(typed value 整路径替换;Rust 侧 set 即覆盖)
        if key.contains("状态覆盖") || key.contains("覆盖变量") {
            let (path, raw_value) = parse_assignment(&value);
            if !path.is_empty() {
                apply_gm_write(state, &path, &raw_value, &mut result, false)?;
            }
            continue;
        }

        // 主线任务更新 / 主线(同时写 main_quest + current_objective)
        if key.contains("主线任务更新") || key.contains("主线") {
            apply_gm_write(state, "memory.main_quest", &value, &mut result, false)?;
            apply_gm_write(state, "memory.current_objective", &value, &mut result, false)?;
            continue;
        }

        // 当前目标 / 目标
        if key.contains("当前目标") || key == "目标" {
            apply_gm_write(state, "memory.current_objective", &value, &mut result, false)?;
            continue;
        }

        // 当前可支配资源 / 资源(list 切分逐条 append)
        if key.contains("当前可支配资源") || key.contains("资源") {
            for part in split_items(&value) {
                apply_gm_write(state, "memory.resources", &part, &mut result, true)?;
            }
            continue;
        }

        // 能力 / 技能 / 掌握
        if key.contains("能力") || key.contains("技能") || key.contains("掌握") {
            apply_gm_write(state, "memory.abilities", &value, &mut result, true)?;
            continue;
        }

        // 关系
        if key.contains("关系") {
            if let Some((name, status)) = split_relation(&value) {
                update_relationship(state, &name, &status);
                result
                    .updates
                    .push(format!("关系:{name} -> {status}"));
            } else {
                // Python 同分支兜底:不像关系格式 → 当 facts append
                apply_gm_write(state, "memory.facts", tag, &mut result, true)?;
            }
            continue;
        }

        // 获得新身份 / 身份 / "你已获得..." 开头
        if key.contains("获得新身份") || key.contains("身份") || tag.starts_with("你已获得") {
            apply_gm_write(state, "memory.facts", tag, &mut result, true)?;
            continue;
        }

        // 默认:不识别的 key 当 facts append
        if !tag.trim().is_empty() {
            apply_gm_write(state, "memory.facts", tag, &mut result, true)?;
        }
    }

    // 4.1) 兜底:GM 正文里抽显式时间推进短语(无【】标签)
    let world_time_now = state.data.world.time.clone();
    for value in extract_explicit_time_updates(gm_text) {
        if value == world_time_now {
            continue;
        }
        if pending_jump_present && asking_for_confirm {
            result
                .updates
                .push(format!("时间提案保留待确认:{value}"));
            continue;
        }
        apply_gm_write(state, "world.time", &value, &mut result, false)?;
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
                Value::Object(_)
                    // 启发式:必须看着像 state op
                    if has_op_key(&parsed) =>
                {
                    ops.push(parsed);
                }
                Value::Object(_) => {
                    stripped_parts.push(m.as_str().to_string());
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
    let tail: Vec<Value> = updates
        .iter()
        .rev()
        .take(12)
        .rev()
        .map(|s| Value::String(s.clone()))
        .collect();
    state.data.memory.last_structured_updates = tail;
}

fn log_op_parse_error(state: &mut GameState, hint: &str) {
    use chrono::Utc;
    use rpg_schemas::AuditEntry;
    let entry = AuditEntry {
        ts: Utc::now().to_rfc3339(),
        source: "gm:json".to_string(),
        hint: Some(hint.to_string()),
        ..Default::default()
    };
    let arr = &mut state.data.permissions.audit_log;
    arr.push(entry);
    let len = arr.len();
    if len > 200 {
        arr.drain(0..len - 200);
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

/// 对应 Python `_parse_assignment(text)` — 切 `+=` / `=` / `：` / `:` 取 (path, value)。
fn parse_assignment(text: &str) -> (String, String) {
    let cleaned = clean_item(text);
    for sep in ["+=", "=", "：", ":"] {
        if let Some(pos) = cleaned.find(sep) {
            let (l, r) = cleaned.split_at(pos);
            let right = &r[sep.len()..];
            let left_clean = crate::path::clean_path(&clean_item(l));
            let right_clean = clean_item(right);
            return (left_clean, right_clean);
        }
    }
    (String::new(), cleaned)
}

/// 对应 Python `_split_items` — 顿号/分号/换行强切;逗号在 ≤12 字短词列表才切。
fn split_items(text: &str) -> Vec<String> {
    let raw = text.trim();
    if raw.is_empty() {
        return Vec::new();
    }
    static STRONG_SEP: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"[、;；\n]\s*").expect("strong sep regex"));
    static COMMA_SEP: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"[,,]\s*").expect("comma sep regex"));
    static SENTENCE_PUNCT: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"[。！？!?]").expect("sentence punct regex"));

    let mut out: Vec<String> = Vec::new();
    for part in STRONG_SEP.split(raw) {
        let trimmed = part.trim();
        if trimmed.is_empty() {
            continue;
        }
        let sub_parts: Vec<&str> = COMMA_SEP
            .split(trimmed)
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .collect();
        let can_split_commas = sub_parts.len() > 1
            && sub_parts.iter().all(|s| {
                clean_item(s).chars().count() <= 12 && !SENTENCE_PUNCT.is_match(s)
            });
        if can_split_commas {
            for s in sub_parts {
                let cleaned = clean_item(s);
                if !cleaned.is_empty() {
                    out.push(cleaned);
                }
            }
        } else {
            let cleaned = clean_item(trimmed);
            if !cleaned.is_empty() {
                out.push(cleaned);
            }
        }
    }
    out
}

/// 对应 Python `set_user_variable(key, value, source)` — 写 worldline.user_variables.{key}。
/// 返回 true 代表值实际有变化(老值不存在或值不同)。
fn set_user_variable(state: &mut GameState, key: &str, value: &str, source: &str) -> bool {
    let key = clean_item(key);
    let value = clean_item(value);
    if key.is_empty() || value.is_empty() {
        return false;
    }
    let turn = state.turn();
    let vars = &mut state.data.worldline.user_variables;
    let old_value = vars
        .get(&key)
        .and_then(|v| v.get("value"))
        .and_then(Value::as_str)
        .map(|s| s.to_string());
    vars.insert(
        key.clone(),
        json!({
            "value": value,
            "source": source,
            "locked": true,
            "turn": turn,
            "updated_at": chrono::Utc::now().to_rfc3339(),
        }),
    );
    state.touch();
    old_value.map(|s| s != value).unwrap_or(true)
}

static TIME_PATTERN_1: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:时间线|时间|剧情|镜头|场景)\s*(?:跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接进入|进入)\s*([^，。！？\n]{2,40})",
    )
    .expect("explicit time pattern 1")
});
static TIME_PATTERN_2: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:时间来到|时间推进至|时间推进到|时间跳至|时间跳到|镜头切到|画面切到|场景切到|场景来到)\s*([^，。！？\n]{2,40})",
    )
    .expect("explicit time pattern 2")
});

/// 对应 Python `_extract_explicit_time_updates` — 从 GM 正文匹配显式时间推进短语。
fn extract_explicit_time_updates(text: &str) -> Vec<String> {
    use crate::timeline_jump::{clean_time_value, looks_like_time_value};
    let mut values: Vec<String> = Vec::new();
    for re in [&*TIME_PATTERN_1, &*TIME_PATTERN_2] {
        for caps in re.captures_iter(text) {
            if let Some(m) = caps.get(1) {
                let cleaned = clean_time_value(m.as_str());
                if looks_like_time_value(&cleaned) && !values.contains(&cleaned) {
                    values.push(cleaned);
                }
            }
        }
    }
    values
}

