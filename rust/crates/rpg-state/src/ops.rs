//! ops.rs — Op 协议 + apply_op 主流程 + 权限闸门
//!
//! 对应 Python:
//! - `rpg/state/_mixins/apply_ops.py::ApplyOpsMixin::apply_state_write_typed`
//! - `rpg/state/path_ops.py::_write_path_hard_forbidden / _rules_managed / _allowed / _kind`
//! - `rpg/state/permissions.py::_normalize_permission_mode / _permission_label`
//!
//! 与 Python 的差异:
//! - 不再做字符串 spec(`path=value`)解析 — 上层 LLM 协议层负责拆解后传 [`Op`]。
//! - 不再走 dispatcher tool 路由(`state_op_tool_map`),那一层在 rpg-tools-dsl
//!   完成后再桥接;此处 fall-through 到老路径(直接 set/append/merge)。
//! - 不再调 `apply_state_write`(字符串 spec) — `Op::Set` 已是 typed value。
//!
//! 主流程:
//! 1. `validate_op` — 形式校验(path 非空、Inc 数值合法)
//! 2. `apply_op` 内闸门顺序:
//!    a. _HARD_FORBIDDEN_PATHS / _HARD_FORBIDDEN_PREFIXES — 任何 source 都拒
//!    b. _RULES_MANAGED_PATHS / _RULES_MANAGED_PREFIXES — 非 rules_engine 拒
//!    c. _MODULE_MANAGED_PATHS — 模组运行时 GM source 拒(scene.module_id 非空)
//!    d. 权限模式白名单 — 不通过 → pending_writes 排队
//!    e. 通过 → 写入 + audit_log + 用户写入 → mark_user_locked

use chrono::Utc;
use once_cell::sync::Lazy;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::atomic::{AtomicU64, Ordering};
use thiserror::Error;

use crate::path::{self, clean_path};
use crate::state::{GameState, StateError};

#[derive(Debug, Error)]
pub enum OpError {
    #[error("op rejected: empty path")]
    EmptyPath,
    #[error("op rejected: hard forbidden path `{0}`")]
    HardForbidden(String),
    #[error("op rejected: rules-managed path `{0}` (source `{1}`)")]
    RulesManaged(String, String),
    #[error("op rejected: module-managed path `{0}` while module scene active")]
    ModuleManaged(String),
    #[error("op rejected: invalid numeric delta")]
    InvalidDelta,
    #[error("op rejected: merge value must be object at `{0}`")]
    MergeNotObject(String),
    #[error("state error: {0}")]
    State(#[from] StateError),
}

/// 写入操作枚举。所有变更必须通过 [`apply_op`] 走完整闸门。
///
/// `path` 在内部统一过 [`clean_path`] 做中文别名 / 空白归一。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Op {
    /// 整路径覆盖。
    Set { path: String, value: Value },
    /// 删除路径(对应 Python `del data[..]`)。
    Delete { path: String },
    /// 数组 append。target 不是 array 自动初始化为 array。
    Append { path: String, value: Value },
    /// 数值 +=。target 非数 当 0。
    Inc { path: String, delta: f64 },
    /// shallow merge Object 字段。
    Merge { path: String, value: Value },
}

impl Op {
    pub fn path(&self) -> &str {
        match self {
            Op::Set { path, .. }
            | Op::Delete { path }
            | Op::Append { path, .. }
            | Op::Inc { path, .. }
            | Op::Merge { path, .. } => path,
        }
    }

    pub fn kind_name(&self) -> &'static str {
        match self {
            Op::Set { .. } => "set",
            Op::Delete { .. } => "delete",
            Op::Append { .. } => "append",
            Op::Inc { .. } => "inc",
            Op::Merge { .. } => "merge",
        }
    }

    pub fn value(&self) -> Option<&Value> {
        match self {
            Op::Set { value, .. } | Op::Append { value, .. } | Op::Merge { value, .. } => {
                Some(value)
            }
            Op::Delete { .. } | Op::Inc { .. } => None,
        }
    }
}

/// 形式校验。深度的语义校验(类型 / 范围)在 apply_op 内部按 op kind 各自处理。
pub fn validate_op(op: &Op) -> Result<(), OpError> {
    if op.path().trim().is_empty() {
        return Err(OpError::EmptyPath);
    }
    if let Op::Inc { delta, .. } = op {
        if !delta.is_finite() {
            return Err(OpError::InvalidDelta);
        }
    }
    if let Op::Merge { value, path } = op {
        if !value.is_object() {
            return Err(OpError::MergeNotObject(path.clone()));
        }
    }
    Ok(())
}

// ─────────────────────────────────────────────────────────────
// 黑白名单(原样搬 Python `rpg/state/path_ops.py`)
// ─────────────────────────────────────────────────────────────

static HARD_FORBIDDEN_PATHS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    ["schema_version", "history", "created_at", "is_new"]
        .into_iter()
        .collect()
});

static HARD_FORBIDDEN_PREFIXES: &[&str] = &["history.", "permissions."];

static RULES_MANAGED_PATHS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "player_character.hp",
        "player_character.max_hp",
        "player_character.ac",
        "player_character.inventory",
        "encounter.active",
        "encounter.round",
        "encounter.turn_index",
        "encounter.initiative_order",
        "encounter.combatants",
        "encounter.encounter_id",
        "encounter.log",
        "dice_log",
    ]
    .into_iter()
    .collect()
});

static RULES_MANAGED_PREFIXES: &[&str] = &[
    "encounter.combatants.",
    "encounter.initiative_order.",
    "dice_log.",
    "player_character.conditions",
    "player_character.inventory.",
];

static MODULE_MANAGED_PATHS: Lazy<HashSet<&'static str>> =
    Lazy::new(|| ["player.current_location"].into_iter().collect());

pub fn is_hard_forbidden(path: &str) -> bool {
    HARD_FORBIDDEN_PATHS.contains(path)
        || HARD_FORBIDDEN_PREFIXES.iter().any(|p| path.starts_with(p))
}

pub fn is_rules_managed(path: &str) -> bool {
    if RULES_MANAGED_PATHS.contains(path) {
        return true;
    }
    RULES_MANAGED_PREFIXES.iter().any(|prefix| {
        let bare = prefix.trim_end_matches('.');
        path == bare || path.starts_with(prefix)
    })
}

pub fn is_module_managed(path: &str) -> bool {
    MODULE_MANAGED_PATHS.contains(path)
}

fn module_scene_active(state: &GameState) -> bool {
    state
        .data
        .get("scene")
        .and_then(|s| s.get("module_id"))
        .and_then(|v| v.as_str())
        .map(|s| !s.is_empty())
        .unwrap_or(false)
}

// ─────────────────────────────────────────────────────────────
// 权限归一(对应 permissions.py)
// ─────────────────────────────────────────────────────────────

pub fn normalize_permission_mode(mode: &str) -> &'static str {
    let trimmed = mode.trim().to_lowercase();
    match trimmed.as_str() {
        "只读" | "只读模式" | "suggest" | "read" | "read_only" | "plan" => "read_only",
        "默认权限" | "default" => "default",
        "auto" | "自动审查" | "auto_review" | "review" => "auto_review",
        "完全访问权限" | "full" | "full_access" => "full_access",
        _ => "full_access",
    }
}

pub fn permission_label(mode: &str) -> &'static str {
    match normalize_permission_mode(mode) {
        "read_only" => "只读模式(仅叙事)",
        "default" => "默认权限",
        "auto_review" => "自动审查",
        _ => "完全访问权限",
    }
}

/// 路径写入是否允许 — 对应 `_write_path_allowed`
pub fn is_write_allowed(path: &str, mode_raw: &str) -> bool {
    let mode = normalize_permission_mode(mode_raw);
    if is_hard_forbidden(path) {
        return false;
    }
    if mode == "read_only" {
        return false;
    }
    if mode == "full_access" {
        return true;
    }
    // UI 子树只允许 full_access
    if path.starts_with("worldline.custom_ui.") || path.starts_with("ui.") {
        return mode == "full_access";
    }
    static ALLOWED: Lazy<HashSet<&'static str>> = Lazy::new(|| {
        [
            "player.name",
            "player.role",
            "player.background",
            "player.current_location",
            "world.time",
            "world.timeline.current_phase",
            "world.timeline.anchor_state",
            "world.known_events",
            "memory.mode",
            "memory.main_quest",
            "memory.current_objective",
            "memory.resources",
            "memory.abilities",
            "memory.facts",
            "memory.pinned",
            "memory.notes",
        ]
        .into_iter()
        .collect()
    });
    if mode == "auto_review" {
        return ALLOWED.contains(path)
            || path.starts_with("relationships.")
            || path.starts_with("worldline.user_variables.");
    }
    if mode == "default" {
        static DEFAULT_ALLOWED: Lazy<HashSet<&'static str>> = Lazy::new(|| {
            [
                "player.current_location",
                "world.time",
                "memory.main_quest",
                "memory.current_objective",
                "memory.resources",
                "memory.abilities",
                "memory.facts",
                "world.known_events",
            ]
            .into_iter()
            .collect()
        });
        return DEFAULT_ALLOWED.contains(path) || path.starts_with("relationships.");
    }
    false
}

// ─────────────────────────────────────────────────────────────
// apply_op 主流程
// ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApplyOutcome {
    pub kind: ApplyKind,
    pub path: String,
    pub message: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ApplyKind {
    Applied,
    Pending,
    Rejected,
}

/// 应用 op 到 state(typed value),走完整闸门。
///
/// `source` 决定信任级别:
/// - `"rules_engine"` 前缀:可写 rules_managed 路径
/// - `"gm"` 前缀:模组运行时不能写 module_managed
/// - `"user*"` 或 `force=true`:写入后自动 mark_user_locked(本实现在 TODO 里)
///
/// `force=true` 不能突破 hard forbidden,但可绕过权限模式白名单(对应 `/set`)。
pub fn apply_op(
    state: &mut GameState,
    op: Op,
    source: &str,
    force: bool,
) -> Result<ApplyOutcome, OpError> {
    validate_op(&op)?;
    let raw_path = op.path().to_string();
    let path = clean_path(&raw_path);

    // 闸门 a: hard forbidden 任何 force 都不能突破
    if is_hard_forbidden(&path) {
        push_audit(
            state,
            json!({
                "ts": Utc::now().to_rfc3339(),
                "source": source,
                "path": path,
                "blocked": "hard_forbidden",
                "turn": state.turn(),
            }),
        );
        return Err(OpError::HardForbidden(path));
    }

    // 闸门 b: rules_managed
    if is_rules_managed(&path) && !source.starts_with("rules_engine") {
        push_audit(
            state,
            json!({
                "ts": Utc::now().to_rfc3339(),
                "source": source,
                "path": path,
                "blocked": "rules_managed",
                "hint": "受规则引擎管理的硬数值(HP/AC/initiative/dice_log)只能由 RulesEngine 写入",
                "turn": state.turn(),
            }),
        );
        return Err(OpError::RulesManaged(path, source.to_string()));
    }

    // 闸门 c: module_managed (模组运行时 GM 不能写)
    if is_module_managed(&path) && module_scene_active(state) && source.starts_with("gm") {
        push_audit(
            state,
            json!({
                "ts": Utc::now().to_rfc3339(),
                "source": source,
                "path": path,
                "blocked": "module_managed",
                "turn": state.turn(),
            }),
        );
        return Err(OpError::ModuleManaged(path));
    }

    // 闸门 d: 权限模式
    let mode_raw = state.permission_mode_raw().to_string();
    let mode = normalize_permission_mode(&mode_raw);
    let allowed = is_write_allowed(&path, &mode_raw);
    if !allowed && !force {
        let pending_id = next_pending_id();
        let from = path::get_path(&state.data, &path).cloned().unwrap_or(Value::Null);
        let value_for_log = op.value().cloned().unwrap_or(Value::Null);
        let pending = json!({
            "id": pending_id,
            "path": path,
            "value": value_for_log,
            "op": op.kind_name(),
            "source": source,
            "turn": state.turn(),
            "from": from,
            "to": value_for_log,
            "reason": format!("{}未授权此字段自动写入", permission_label(&mode_raw)),
        });
        push_pending(state, pending);
        return Ok(ApplyOutcome {
            kind: ApplyKind::Pending,
            path: path.clone(),
            message: format!("状态写入待审:{path}"),
        });
    }

    // 闸门 e: 通过 — 实际写入
    perform_write(state, &op, &path)?;

    // 用户写入登记 user_locked_fields(对应 task 36)
    if force || source.starts_with("user") {
        mark_user_locked(state, &path);
    }

    push_audit(
        state,
        json!({
            "ts": Utc::now().to_rfc3339(),
            "source": source,
            "path": path,
            "op": op.kind_name(),
            "value": op.value().cloned().unwrap_or(Value::Null),
            "mode": mode,
            "turn": state.turn(),
        }),
    );

    Ok(ApplyOutcome {
        kind: ApplyKind::Applied,
        path: path.clone(),
        message: format!("状态写入:{path}"),
    })
}

fn perform_write(state: &mut GameState, op: &Op, path: &str) -> Result<(), StateError> {
    match op {
        Op::Set { value, .. } => state.set_path(path, value.clone()),
        Op::Delete { .. } => {
            state.delete_path(path)?;
            Ok(())
        }
        Op::Append { value, .. } => state.append_to_path(path, value.clone()),
        Op::Inc { delta, .. } => {
            state.inc_path(path, *delta)?;
            Ok(())
        }
        Op::Merge { value, .. } => state.merge_path(path, value.clone()),
    }
}

fn push_audit(state: &mut GameState, entry: Value) {
    let permissions = ensure_object_at(&mut state.data, "permissions");
    let audit_log = permissions
        .entry("audit_log".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Value::Array(arr) = audit_log {
        arr.push(entry);
        let len = arr.len();
        if len > 200 {
            arr.drain(0..len - 200);
        }
    }
}

fn push_pending(state: &mut GameState, entry: Value) {
    let permissions = ensure_object_at(&mut state.data, "permissions");
    let pending = permissions
        .entry("pending_writes".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Value::Array(arr) = pending {
        arr.push(entry);
        let len = arr.len();
        if len > 20 {
            arr.drain(0..len - 20);
        }
    }
}

/// 顶层 obj 上保证存在某个 key,返回对应的 Object map 引用。
fn ensure_object_at<'a>(
    root: &'a mut Value,
    key: &str,
) -> &'a mut serde_json::Map<String, Value> {
    if !root.is_object() {
        *root = Value::Object(serde_json::Map::new());
    }
    let obj = root.as_object_mut().expect("ensured object");
    if !obj.get(key).map(Value::is_object).unwrap_or(false) {
        obj.insert(key.to_string(), Value::Object(serde_json::Map::new()));
    }
    obj.get_mut(key)
        .and_then(Value::as_object_mut)
        .expect("just inserted object")
}

/// Pending 写入 ID 生成器(进程内单调递增 + 启动时间戳前缀)。
/// 不需要密码学随机,只要在单进程 + 重启间唯一即可;前端按 id 审批,会话内不冲突。
static PENDING_COUNTER: AtomicU64 = AtomicU64::new(0);

fn next_pending_id() -> String {
    let seq = PENDING_COUNTER.fetch_add(1, Ordering::Relaxed);
    let ts = Utc::now().timestamp_millis() as u64;
    format!("pw_{ts:x}_{seq:x}")
}

/// 把 path 登记到 player_private.user_locked_fields(去重 append)
/// TODO[Opus]: 与 PendingMixin 的精细化登记策略对齐(目前实现只是去重 append)。
fn mark_user_locked(state: &mut GameState, path: &str) {
    let pp = ensure_object_at(&mut state.data, "player_private");
    let locked = pp
        .entry("user_locked_fields".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Value::Array(arr) = locked {
        let already = arr
            .iter()
            .any(|v| v.as_str().map(|s| s == path).unwrap_or(false));
        if !already {
            arr.push(Value::String(path.to_string()));
        }
    }
}
