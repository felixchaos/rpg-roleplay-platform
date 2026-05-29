//! rpg-schemas — Pydantic → serde 数据模型
//! 对应 Python: rpg/schemas/

pub mod common;
pub mod console_assistant;
pub mod core;
pub mod game;
pub mod game_state;
pub mod mcp;
pub mod memory;
pub mod models;
pub mod permissions;
pub mod rules;
pub mod skills;
pub mod timeline;
pub mod worldline;

// 顶层 typed schema(待办E)— 给 rpg-state 接管 GameState.data,给 ts-rs 导出前端类型。
pub use game_state::{
    default_data_json, AuditEntry, Encounter, GameStateData, Memory, PendingWrite,
    PermissionsState, PlayerCharacter, PlayerInfo, PlayerPrivate, Ruleset, Scene, TimelineState,
    World, Worldline, WorldlineValidation,
};
