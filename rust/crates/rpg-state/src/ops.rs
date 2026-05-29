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
use phf::phf_set;
use rpg_schemas::{AuditEntry, PendingWrite};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};
use thiserror::Error;

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

use crate::path::clean_path;
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
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
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

/// 编译期完美哈希集合 — 精确路径黑名单(前缀匹配另有 HARD_FORBIDDEN_PREFIXES)。
static HARD_FORBIDDEN_PATHS: phf::Set<&'static str> =
    phf_set! { "schema_version", "history", "created_at", "is_new" };

static HARD_FORBIDDEN_PREFIXES: &[&str] = &["history.", "permissions."];

/// 编译期完美哈希集合 — 规则引擎管理的精确路径。
static RULES_MANAGED_PATHS: phf::Set<&'static str> = phf_set! {
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
    "dice_log"
};

static RULES_MANAGED_PREFIXES: &[&str] = &[
    "encounter.combatants.",
    "encounter.initiative_order.",
    "dice_log.",
    "player_character.conditions",
    "player_character.inventory.",
];

/// 编译期完美哈希集合 — 模组运行时管理的精确路径。
static MODULE_MANAGED_PATHS: phf::Set<&'static str> =
    phf_set! { "player.current_location" };

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
    !state.data.scene.module_id.is_empty()
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
    static ALLOWED: phf::Set<&'static str> = phf_set! {
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
        "memory.notes"
    };
    if mode == "auto_review" {
        return ALLOWED.contains(path)
            || path.starts_with("relationships.")
            || path.starts_with("worldline.user_variables.");
    }
    if mode == "default" {
        static DEFAULT_ALLOWED: phf::Set<&'static str> = phf_set! {
            "player.current_location",
            "world.time",
            "memory.main_quest",
            "memory.current_objective",
            "memory.resources",
            "memory.abilities",
            "memory.facts",
            "world.known_events"
        };
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
#[tracing::instrument(
    skip(state, op),
    fields(
        user_id = %state.user_id,
        op_type = ?std::mem::discriminant(&op),
        path = %op.path(),
        source = %source,
        force = force,
    )
)]
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
            AuditEntry::blocked(source, &path, "hard_forbidden", state.turn().max(0) as u64),
        );
        return Err(OpError::HardForbidden(path));
    }

    // 闸门 b: rules_managed
    if is_rules_managed(&path) && !source.starts_with("rules_engine") {
        push_audit(
            state,
            AuditEntry::blocked_with_hint(
                source,
                &path,
                "rules_managed",
                "受规则引擎管理的硬数值(HP/AC/initiative/dice_log)只能由 RulesEngine 写入",
                state.turn().max(0) as u64,
            ),
        );
        return Err(OpError::RulesManaged(path, source.to_string()));
    }

    // 闸门 c: module_managed (模组运行时 GM 不能写)
    if is_module_managed(&path) && module_scene_active(state) && source.starts_with("gm") {
        push_audit(
            state,
            AuditEntry::blocked(source, &path, "module_managed", state.turn().max(0) as u64),
        );
        return Err(OpError::ModuleManaged(path));
    }

    // 闸门 d: 权限模式
    let mode_raw = state.permission_mode_raw().to_string();
    let mode = normalize_permission_mode(&mode_raw);
    let allowed = is_write_allowed(&path, &mode_raw);
    if !allowed && !force {
        let pending_id = next_pending_id();
        let from = crate::typed_path::get_path(&state.data, &path).unwrap_or(Value::Null);
        let value_for_log = op.value().cloned().unwrap_or(Value::Null);
        let pending = PendingWrite {
            id: pending_id,
            path: path.clone(),
            value: value_for_log.clone(),
            op: op.kind_name().to_string(),
            source: source.to_string(),
            turn: state.turn().max(0) as u64,
            from,
            to: value_for_log,
            reason: format!("{}未授权此字段自动写入", permission_label(&mode_raw)),
            extra: Default::default(),
        };
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
        AuditEntry::applied(
            source,
            &path,
            op.kind_name(),
            op.value().cloned().unwrap_or(Value::Null),
            mode,
            state.turn().max(0) as u64,
        ),
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

/// 落 audit_log — 直 typed push,cap 200。委托给 [`crate::typed_path::push_audit`]。
fn push_audit(state: &mut GameState, entry: AuditEntry) {
    crate::typed_path::push_audit(&mut state.data, entry);
}

fn push_pending(state: &mut GameState, entry: PendingWrite) {
    crate::typed_path::push_pending(&mut state.data, entry);
}

/// Pending 写入 ID 生成器(进程内单调递增 + 启动时间戳前缀)。
/// 不需要密码学随机,只要在单进程 + 重启间唯一即可;前端按 id 审批,会话内不冲突。
static PENDING_COUNTER: AtomicU64 = AtomicU64::new(0);

fn next_pending_id() -> String {
    let seq = PENDING_COUNTER.fetch_add(1, Ordering::Relaxed);
    let ts = Utc::now().timestamp_millis() as u64;
    format!("pw_{ts:x}_{seq:x}")
}

/// 把 path 登记到 player_private.user_locked_fields(去重 append)。
/// 字段是 `PlayerPrivate.extra` 里的运行时附加键(task 36 加的,非原 schema)。
fn mark_user_locked(state: &mut GameState, path: &str) {
    crate::typed_path::mark_user_locked(&mut state.data, path);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::GameState;
    use serde_json::json;

    fn make_state() -> GameState {
        GameState::new("test_user")
    }

    fn set_permission_mode(state: &mut GameState, mode: &str) {
        state.data.permissions.mode = mode.to_string();
    }

    fn set_module_scene(state: &mut GameState, module_id: &str) {
        state.data.scene.module_id = module_id.to_string();
        // 保留旧测试结构 — 第二行 noop,避免触发未使用变量警告
        let _ = module_id;
    }

    fn audit_log_len(state: &GameState) -> usize {
        state.data.permissions.audit_log.len()
    }

    fn pending_writes_len(state: &GameState) -> usize {
        state.data.permissions.pending_writes.len()
    }

    // ── 闸门 a: hard forbidden ──────────────────────────────────

    #[test]
    fn test_hard_forbidden_blocks_all() {
        // history.* / permissions.* 任何 source 都拒
        let mut state = make_state();
        for source in ["user", "gm", "rules_engine", "user:/set", "anything"] {
            let op = Op::Set {
                path: "history.foo".to_string(),
                value: json!("x"),
            };
            let err = apply_op(&mut state, op, source, false).unwrap_err();
            assert!(
                matches!(err, OpError::HardForbidden(_)),
                "source={source} not blocked: {err:?}"
            );
        }
        // permissions.* prefix
        let op = Op::Set {
            path: "permissions.mode".to_string(),
            value: json!("read_only"),
        };
        let err = apply_op(&mut state, op, "rules_engine", false).unwrap_err();
        assert!(matches!(err, OpError::HardForbidden(_)));
        // schema_version 整路径
        let op = Op::Set {
            path: "schema_version".to_string(),
            value: json!(999),
        };
        let err = apply_op(&mut state, op, "rules_engine", false).unwrap_err();
        assert!(matches!(err, OpError::HardForbidden(_)));
    }

    #[test]
    fn test_hard_forbidden_blocks_even_with_force() {
        // force=true 也不能突破 hard forbidden
        let mut state = make_state();
        let op = Op::Set {
            path: "history.foo".to_string(),
            value: json!("x"),
        };
        let err = apply_op(&mut state, op, "user:/set", /*force=*/ true).unwrap_err();
        assert!(matches!(err, OpError::HardForbidden(_)));
    }

    // ── 闸门 b: rules_managed ────────────────────────────────────

    #[test]
    fn test_rules_managed_only_rules_engine() {
        // HP / encounter.* 只 rules_engine source 可写
        let mut state = make_state();

        // gm source 写 player_character.hp 拒绝
        let op = Op::Set {
            path: "player_character.hp".to_string(),
            value: json!(10),
        };
        let err = apply_op(&mut state, op, "gm", false).unwrap_err();
        assert!(matches!(err, OpError::RulesManaged(_, _)));

        // user source 写 encounter.round 拒绝
        let op = Op::Set {
            path: "encounter.round".to_string(),
            value: json!(2),
        };
        let err = apply_op(&mut state, op, "user", true).unwrap_err();
        assert!(matches!(err, OpError::RulesManaged(_, _)));

        // rules_engine 写 player_character.hp 通过
        let op = Op::Set {
            path: "player_character.hp".to_string(),
            value: json!(15),
        };
        let outcome = apply_op(&mut state, op, "rules_engine", false).unwrap();
        assert_eq!(outcome.kind, ApplyKind::Applied);
        assert_eq!(state.get_path("player_character.hp"), Some(json!(15)));
    }

    // ── 闸门 c: module_managed ───────────────────────────────────

    #[test]
    fn test_module_managed_blocks_gm_player_location() {
        // 有 module_id 时 gm* source 拒 player.current_location
        let mut state = make_state();
        set_module_scene(&mut state, "the_cave");
        let op = Op::Set {
            path: "player.current_location".to_string(),
            value: json!("洞穴"),
        };
        let err = apply_op(&mut state, op, "gm", false).unwrap_err();
        assert!(matches!(err, OpError::ModuleManaged(_)));

        // 没有 module_id 时 gm 可以写
        let mut state2 = make_state();
        let op2 = Op::Set {
            path: "player.current_location".to_string(),
            value: json!("城镇"),
        };
        let outcome = apply_op(&mut state2, op2, "gm", false).unwrap();
        assert_eq!(outcome.kind, ApplyKind::Applied);
    }

    // ── 闸门 d: 权限模式 ────────────────────────────────────────

    #[test]
    fn test_permission_default_pushes_pending() {
        // default 模式下,未授权字段 → pending_writes,不直接生效
        let mut state = make_state();
        set_permission_mode(&mut state, "default");

        // player.name 不在 default 白名单 → pending
        let op = Op::Set {
            path: "player.name".to_string(),
            value: json!("Bob"),
        };
        let outcome = apply_op(&mut state, op, "gm", false).unwrap();
        assert_eq!(outcome.kind, ApplyKind::Pending);
        // 路径未生效
        assert_eq!(state.get_path("player.name"), Some(json!("")));
        assert_eq!(pending_writes_len(&state), 1);
    }

    #[test]
    fn test_permission_full_access_passes() {
        // full_access 模式 → 直接生效
        let mut state = make_state();
        set_permission_mode(&mut state, "full_access");
        let op = Op::Set {
            path: "player.name".to_string(),
            value: json!("Carol"),
        };
        let outcome = apply_op(&mut state, op, "gm", false).unwrap();
        assert_eq!(outcome.kind, ApplyKind::Applied);
        assert_eq!(state.get_path("player.name"), Some(json!("Carol")));
        assert_eq!(pending_writes_len(&state), 0);
    }

    // ── audit_log / pending_writes 容量 ─────────────────────────

    #[test]
    fn test_audit_log_capped_200() {
        // 写 250 次只保留最新 200
        let mut state = make_state();
        for i in 0..250 {
            let op = Op::Set {
                path: "player.name".to_string(),
                value: json!(format!("name_{i}")),
            };
            // user source + force=true 保证全部通过闸门
            let outcome = apply_op(&mut state, op, "user", true).unwrap();
            assert_eq!(outcome.kind, ApplyKind::Applied);
        }
        assert_eq!(audit_log_len(&state), 200);
    }

    #[test]
    fn test_pending_writes_capped_20() {
        // 30 次 pending 只保留最新 20
        let mut state = make_state();
        set_permission_mode(&mut state, "default");
        for i in 0..30 {
            let op = Op::Set {
                path: "player.name".to_string(),
                value: json!(format!("name_{i}")),
            };
            let outcome = apply_op(&mut state, op, "gm", false).unwrap();
            assert_eq!(outcome.kind, ApplyKind::Pending);
        }
        assert_eq!(pending_writes_len(&state), 20);
    }
}
