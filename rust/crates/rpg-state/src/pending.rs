//! pending.rs — pending_writes 审批 mixin
//!
//! 对应 Python: `rpg/state/_mixins/pending.py::PendingMixin`。
//! 当前只迁 approve / reject / pop / 询问入队 / 询问过期 / 询问清理 6 个核心方法,
//! `expire_stale_gm_questions` / `clear_pending_question` 一并迁过来,这样
//! pending 整套生命周期都在一个 module。
//!
//! 与 Python 差异:
//! - approve 不再调 `apply_state_write_typed`(Rust 侧已是 typed `Op`)。
//!   直接构造 [`crate::ops::Op`] + `force=true` 走 [`crate::ops::apply_op`]。
//!   `Op::Append` 对应 Python `append=true`;`Op::Set` 对应 set;
//!   Python 里 `overwrite` 只影响 `_split_items` list-append vs 完整覆盖,
//!   Rust 侧 typed value 直接传 — 是 list 就 set 整个 list,是 scalar 就 set
//!   scalar,append 永远只是单元素 push。
//! - reject 不再调 `_normalize_permission_mode` 重新计算 mode 写进 audit;
//!   直接用 state 上读到的当前 mode 字符串,保持原行为可读性。

use chrono::Utc;
use serde_json::{json, Value};
use thiserror::Error;

use crate::ops::{self, ApplyKind, ApplyOutcome, Op, OpError};
use crate::state::GameState;

#[derive(Debug, Error)]
pub enum PendingError {
    #[error("pending entry not found: {0}")]
    NotFound(String),
    #[error("op error during apply: {0}")]
    Op(#[from] OpError),
}

/// 审批结果,返回给上层用作 UI 反馈。
#[derive(Debug, Clone)]
pub struct ApproveResult {
    pub outcome: ApplyOutcome,
    pub message: String,
}

#[derive(Debug, Clone)]
pub struct RejectResult {
    pub path: String,
    pub message: String,
}

/// 按 id (优先) 或 index 弹出一条 pending_writes,无命中返回 None。
///
/// 对应 Python `_pop_pending_write`。注意 Python 用 index 兜底,Rust 这里也
/// 保留,但调用方应优先用 id —— index 在并发审批下会因为 pop 漂移。
pub fn pop_pending_write(
    state: &mut GameState,
    id: Option<&str>,
    index: Option<usize>,
) -> Option<Value> {
    let arr = &mut state.data.permissions.pending_writes;
    if let Some(target_id) = id {
        if let Some(pos) = arr.iter().position(|item| item.id == target_id) {
            let item = arr.remove(pos);
            return Some(serde_json::to_value(item).unwrap_or(Value::Null));
        }
        return None;
    }
    if let Some(i) = index {
        if i < arr.len() {
            let item = arr.remove(i);
            return Some(serde_json::to_value(item).unwrap_or(Value::Null));
        }
    }
    None
}

/// 审批通过:从 pending_writes 取出条目 → 走 [`apply_op`] 强制写入。
///
/// 对应 Python `approve_pending_write(index, id=...)`。
pub fn approve_pending_write(
    state: &mut GameState,
    id: Option<&str>,
    index: Option<usize>,
) -> Result<ApproveResult, PendingError> {
    let Some(item) = pop_pending_write(state, id, index) else {
        return Err(PendingError::NotFound(
            id.map(|s| s.to_string())
                .unwrap_or_else(|| index.map(|i| i.to_string()).unwrap_or_default()),
        ));
    };
    let path = item
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let value = item.get("value").cloned().unwrap_or(Value::Null);
    let op_kind = item
        .get("op")
        .and_then(Value::as_str)
        .unwrap_or("set")
        .to_string();
    let append_flag = item
        .get("append")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let source_orig = item
        .get("source")
        .and_then(Value::as_str)
        .unwrap_or("gm")
        .to_string();
    let source = format!("{source_orig}:approved");

    // Python `op` 字段是 kind_name(set/append/delete/inc/merge);Rust 侧也走 enum。
    // append_flag 是老 spec 通道遗留,与 `op == "append"` 等价;两者取并集。
    let op = if op_kind == "append" || append_flag {
        Op::Append {
            path: path.clone(),
            value,
        }
    } else if op_kind == "delete" {
        Op::Delete { path: path.clone() }
    } else if op_kind == "inc" {
        let delta = item
            .get("delta")
            .and_then(Value::as_f64)
            .unwrap_or_else(|| value.as_f64().unwrap_or(0.0));
        Op::Inc {
            path: path.clone(),
            delta,
        }
    } else if op_kind == "merge" {
        Op::Merge {
            path: path.clone(),
            value,
        }
    } else {
        Op::Set {
            path: path.clone(),
            value,
        }
    };

    let outcome = ops::apply_op(state, op, &source, /*force=*/ true)?;
    let message = match outcome.kind {
        ApplyKind::Applied => format!("状态写入(审批通过):{path}"),
        ApplyKind::Pending => format!("仍待审:{path}"),
        ApplyKind::Rejected => format!("审批后仍被拒:{path}"),
    };
    Ok(ApproveResult { outcome, message })
}

/// 审批拒绝:弹出 pending_writes 条目 → 写一条 audit_log 留痕。
///
/// 对应 Python `reject_pending_write(index, id=...)`,不带 reason 参数 — Python
/// 原版也只是写默认 audit。`reason` 留个口供上层填业务文案。
pub fn reject_pending_write(
    state: &mut GameState,
    id: Option<&str>,
    index: Option<usize>,
    reason: Option<&str>,
) -> Result<RejectResult, PendingError> {
    let Some(item) = pop_pending_write(state, id, index) else {
        return Err(PendingError::NotFound(
            id.map(|s| s.to_string())
                .unwrap_or_else(|| index.map(|i| i.to_string()).unwrap_or_default()),
        ));
    };
    let path = item
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let value = item.get("value").cloned().unwrap_or(Value::Null);
    let source_orig = item
        .get("source")
        .and_then(Value::as_str)
        .unwrap_or("gm")
        .to_string();
    let mode = state.permission_mode_raw().to_string();
    let mut entry = json!({
        "ts": Utc::now().to_rfc3339(),
        "path": path,
        "value": value,
        "source": format!("{source_orig}:rejected"),
        "mode": mode,
        "turn": state.turn(),
    });
    if let Some(r) = reason {
        entry["reason"] = Value::String(r.to_string());
    }
    push_audit(state, entry);
    Ok(RejectResult {
        path: path.clone(),
        message: format!("状态写入拒绝:{path}"),
    })
}

/// GM 询问入队 — 对应 Python `add_pending_question`。
///
/// `options` None 时不再调 Python `_parse_question` 拆"｜"分隔的 inline 选项 —
/// 那段语义复杂且在 LLM 现代 JSON 协议下基本不再用,等真正需要再回头补。
pub fn add_pending_question(
    state: &mut GameState,
    text: &str,
    source: &str,
    options: Option<Vec<String>>,
) -> bool {
    let question = clean_item(text);
    if question.is_empty() {
        return false;
    }
    let opts: Vec<String> = options
        .unwrap_or_default()
        .into_iter()
        .filter_map(|o| {
            let cleaned = clean_item(&o);
            if cleaned.is_empty() {
                None
            } else {
                Some(cleaned)
            }
        })
        .take(4)
        .collect();
    // 先把 state.turn 取出,避免后面同时 mutable+immutable borrow。
    let turn = state.turn();
    let arr = &mut state.data.permissions.pending_questions;
    // 去重:同 question + 同 options 算重复。
    let already = arr.iter().any(|q| {
        q.get("question")
            .and_then(Value::as_str)
            .map(|s| s == question)
            .unwrap_or(false)
            && q.get("options")
                .and_then(Value::as_array)
                .map(|existing| {
                    existing.len() == opts.len()
                        && existing.iter().zip(opts.iter()).all(|(a, b)| {
                            a.as_str().map(|s| s == b).unwrap_or(false)
                        })
                })
                .unwrap_or(opts.is_empty())
    });
    if already {
        return false;
    }
    let id = next_pending_question_id();
    let entry = json!({
        "id": id,
        "question": question,
        "options": opts,
        "source": source,
        "turn": turn,
    });
    arr.push(entry);
    let len = arr.len();
    if len > 8 {
        arr.drain(0..len - 8);
    }
    true
}

/// 过期旧 GM 询问(玩家新一轮时调)。返回过期了几条。
///
/// 对应 Python `expire_stale_gm_questions`。
pub fn expire_stale_gm_questions(
    state: &mut GameState,
    current_turn: Option<i64>,
    reason: &str,
) -> usize {
    let turn_snapshot = state.turn();
    let cur = current_turn.unwrap_or(turn_snapshot);
    if state.data.permissions.pending_questions.is_empty() {
        return 0;
    }
    let system_sources: &[&str] = &[
        "gm",
        "rules_engine",
        "curator",
        "extractor",
        "set_parser",
    ];
    let mut keep: Vec<Value> = Vec::with_capacity(state.data.permissions.pending_questions.len());
    let mut expired: Vec<Value> = Vec::new();
    for q in state.data.permissions.pending_questions.drain(..) {
        let q_turn = q.get("turn").and_then(Value::as_i64).unwrap_or(0);
        let q_source = q
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let is_system = system_sources
            .iter()
            .any(|s| q_source == *s || q_source.starts_with(&format!("{s}:")));
        if is_system && q_turn < cur {
            expired.push(q);
        } else {
            keep.push(q);
        }
    }
    state.data.permissions.pending_questions = keep;
    if expired.is_empty() {
        return 0;
    }
    let count = expired.len();
    let summary: Vec<Value> = expired
        .iter()
        .map(|q| {
            json!({
                "id": q.get("id").cloned().unwrap_or(Value::Null),
                "turn": q.get("turn").cloned().unwrap_or(Value::Null),
                "source": q.get("source").cloned().unwrap_or(Value::Null),
                "question": q.get("question")
                    .and_then(Value::as_str)
                    .map(|s| s.chars().take(80).collect::<String>())
                    .unwrap_or_default(),
            })
        })
        .collect();
    push_audit(
        state,
        json!({
            "ts": Utc::now().to_rfc3339(),
            "kind": "pending_questions_expired",
            "source": "expire_stale_gm_questions",
            "reason": reason,
            "current_turn": cur,
            "expired_count": count,
            "expired": summary,
            "turn": turn_snapshot,
        }),
    );
    count
}

/// 玩家回答 / 跳过 pending_question。返回被弹出的条目原值。
///
/// 对应 Python `clear_pending_question(index, id=..., choice=...)`。
pub fn clear_pending_question(
    state: &mut GameState,
    id: Option<&str>,
    index: Option<usize>,
    choice: Option<&str>,
) -> Option<Value> {
    let turn_snapshot = state.turn();
    let arr = &mut state.data.permissions.pending_questions;
    let popped = if let Some(target_id) = id {
        let pos = arr.iter().position(|q| {
            q.get("id")
                .and_then(Value::as_str)
                .map(|s| s == target_id)
                .unwrap_or(false)
        })?;
        Some(arr.remove(pos))
    } else if let Some(i) = index {
        if i < arr.len() {
            Some(arr.remove(i))
        } else {
            None
        }
    } else {
        None
    };
    let popped = popped?;
    let question_text = popped
        .get("question")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let source = popped
        .get("source")
        .and_then(Value::as_str)
        .unwrap_or("gm")
        .to_string();
    push_audit(
        state,
        json!({
            "ts": Utc::now().to_rfc3339(),
            "kind": "question_answered",
            "question": question_text,
            "choice": choice.unwrap_or("(skipped)"),
            "source": source,
            "turn": turn_snapshot,
        }),
    );
    Some(popped)
}

// ─────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────

fn push_audit(state: &mut GameState, entry: Value) {
    use rpg_schemas::AuditEntry;
    let audit_entry: AuditEntry = serde_json::from_value(entry).unwrap_or_default();
    let arr = &mut state.data.permissions.audit_log;
    arr.push(audit_entry);
    let len = arr.len();
    if len > 200 {
        arr.drain(0..len - 200);
    }
}

/// SM-18: Parse question text for inline options.
/// Mirrors Python `_parse_question` (parsers.py:72-99).
/// Splits on '|' or '｜', cleans each option.
/// Returns (question_text, options_vec).
pub fn parse_question(text: &str) -> (String, Vec<String>) {
    let text = text.trim();
    if text.is_empty() {
        return (String::new(), vec![]);
    }

    // Try splitting on fullwidth '｜' first, then ASCII '|'
    let parts: Vec<&str> = if text.contains('｜') {
        text.splitn(2, '｜').collect()
    } else if text.contains('|') {
        text.splitn(2, '|').collect()
    } else {
        return (clean_item(text), vec![]);
    };

    if parts.len() < 2 {
        return (clean_item(text), vec![]);
    }

    let question = clean_item(parts[0]);
    let opts_text = parts[1];

    // Split options by the same delimiter
    let delimiter = if text.contains('｜') { '｜' } else { '|' };
    let options: Vec<String> = opts_text
        .split(delimiter)
        .map(|o| clean_item(o))
        .filter(|o| !o.is_empty())
        .take(4)
        .collect();

    (question, options)
}

/// 对应 Python `_clean_item`:strip 头尾空白/冒号/分隔符,折叠中间空白。
pub(crate) fn clean_item(text: &str) -> String {
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

/// pending_question id — 与 ops::next_pending_id 共享思路:进程内单调 + ts。
fn next_pending_question_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
    let ts = Utc::now().timestamp_millis() as u64;
    format!("pq_{ts:x}_{seq:x}")
}
