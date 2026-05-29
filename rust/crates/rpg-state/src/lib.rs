//! rpg-state — GameState 与 op 协议
//!
//! 对应 Python: `rpg/state.py` + `rpg/state/` 子模块(`core` / `path_ops` /
//! `permissions` / `_mixins/apply_ops` / `_mixins/pending` / `_mixins/rules_gameplay`)。
//!
//! 设计决策(见 rust-migration 设计文档):
//! - `GameState.data` 用 `serde_json::Value` 持有顶层 Object,保留 Python 侧
//!   字段的运行时灵活性,迁移期不强行收紧 schema。
//! - 字符串路径写入 → JSON Pointer 风格 op(`Op::Set` / `Append` / `Inc` / `Merge` / `Delete`)。
//! - Python 侧 mixin 多继承 → 平拍成单一 `impl GameState` + 模块函数。
//! - 全局 `_state_by_user` → [`store::StateStore`](Arc<DashMap<String, Arc<RwLock<GameState>>>>),
//!   消除 service locator。key 用 `String` 而非 `rpg_core::UserId`:需容纳匿名
//!   哨兵 `"anonymous"`(详见 [`store`] 模块头注释的取舍说明)。
//!
//! 实现深度(本 PR):
//! - `path` — 完整。dot/bracket 解析 + get/set/delete/append + 中文别名归一。
//! - `state` — 完整数据 + DEFAULT_STATE + 基础 path 访问 + version/touch。
//!   `_migrate` 升级链(v1→v6 + 最终 backfill)由 [`migrate`] 模块提供,
//!   `from_value` 反序列化后无条件跑一遍。
//! - `ops` — 完整。apply_op 走全五道闸门(hard / rules_managed / module_managed /
//!   权限模式 / 通过)+ audit_log + pending_writes + user_locked 登记。
//!   未接 dispatcher 路由(rpg-tools-dsl 完成后补)。
//! - `store` — 完整。get_or_create / get / insert / remove。持久化 TODO。
//!
//! 已迁移的子模块(本 PR):
//! - `structured` — `apply_structured_updates`(【…】 标签 + ```json``` ops 抽取)。
//! - `directives` — `apply_player_directives` / `apply_set_directive`(/set + /reveal)。
//! - `pending` — `approve_pending_write` / `reject_pending_write` + 询问队列生命周期。
//! - `rules_gameplay` — `add_memory_item` / `update_relationship` / `record_hypothesis`。
//! - `timeline_jump` — `request_time_jump` / `confirm_time_jump` / `reject_time_jump`。
//! - `script_overrides` — `load_script_overrides`(save_id → script_id → DB,
//!   走 rpg-db::repos::script_overrides,raw SQL 兜底)。
//!
//! 已迁移的子模块(W3-2 补完):
//! - `worldline_validation` — `_scan_worldline_validation` /
//!   `_set_worldline_validation` / `_store_worldline_projection`。
//! - `combat_state` — RulesEngine 入口:`update_active_entities` /
//!   `append_dice_log` / `update_encounter` / `upsert_active_entity` /
//!   `prune_active_entities` / `clear_encounter`。
//! - `bus` — `StateEventBus` + `StateEvent` 广播(tokio::sync::broadcast),
//!   嵌入 `StateStore`,apply_op 后 publish。

pub mod bus;
pub mod combat_state;
pub mod directives;
pub mod migrate;
pub mod ops;
pub mod path;
pub mod pending;
pub mod rules_gameplay;
pub mod script_overrides;
pub mod state;
pub mod store;
pub mod structured;
pub mod timeline_jump;
pub mod typed_path;
pub mod worldline_validation;

pub use bus::{StateEvent, StateEventBus};
pub use combat_state::{
    append_dice_log, clear_encounter, prune_active_entities, update_active_entities,
    update_encounter, upsert_active_entity, CombatStateError,
};
pub use directives::{
    apply_player_directives, apply_set_directive, parse_assignment, DirectiveError,
    DirectiveResult,
};
pub use migrate::{migrate, MigrateError};
pub use worldline_validation::{
    scan_worldline_validation, set_worldline_validation, store_worldline_projection,
    validation_label, ValidationScan, WorldlineValidationError,
};
pub use ops::{
    apply_op, is_hard_forbidden, is_module_managed, is_rules_managed, is_write_allowed,
    normalize_permission_mode, permission_label, validate_op, ApplyKind, ApplyOutcome, Op,
    OpError,
};
pub use path::{clean_path, parse_path, PathError, PathSegment};
pub use pending::{
    add_pending_question, approve_pending_write, clear_pending_question,
    expire_stale_gm_questions, pop_pending_write, reject_pending_write, ApproveResult,
    PendingError, RejectResult,
};
pub use rules_gameplay::{
    add_memory_item, confirm_hypothesis, record_hypothesis, reject_hypothesis,
    update_relationship, AddMemoryItemArgs, MemoryItem, RulesGameplayError,
};
pub use script_overrides::{
    load_for_script, load_script_overrides, ScriptOverridesError, ScriptOverridesPayload,
};
pub use state::{default_state, GameState, StateError, CURRENT_SCHEMA_VERSION};
pub use store::{SharedState, StateStore};
pub use structured::{
    apply_structured_updates, extract_json_state_ops, StructuredError, UpdateResult,
};
pub use timeline_jump::{
    clean_time_value, confirm_time_jump, is_time_key, looks_like_time_value,
    reject_time_jump, request_time_jump, TimelineJumpError, TimelineJumpResult,
};
