//! GameState 顶层 typed schema — 把热子树从 `serde_json::Value` 抽出。
//!
//! 设计要点:
//! - 顶层 `GameStateData` 用 `#[serde(default)] + #[serde(flatten)] extra` 前向兼容:
//!   未知顶层字段进 `extra`,老存档反序列化不丢字段。
//! - "稳定子树"用 typed struct;"真动态" 容器(`relationships` / `dice_log` /
//!   `worldline.user_variables` 等)保留 `Value` / `Map<String, Value>`。
//! - 所有 struct 都派生 `Default`,并与 [`crate::game_state::default_data`] 输出的
//!   `serde_json::Value`(对齐旧 `rpg-state::default_state()`)做往返一致性单测。
//! - 待办F(ts-rs)会基于本文件统一导出前端类型。

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

// ===== 顶层 =====================================================================

/// 与旧 `rpg-state::default_state()` 同形的顶层游戏状态数据。
///
/// 字段顺序刻意贴近旧 JSON,序列化输出友好。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct GameStateData {
    #[serde(default = "default_schema_version")]
    pub schema_version: u64,
    #[serde(default)]
    pub ruleset: Ruleset,
    #[serde(default)]
    pub player_character: PlayerCharacter,
    #[serde(default)]
    pub scene: Scene,
    #[serde(default)]
    pub encounter: Encounter,
    #[serde(default)]
    pub dice_log: Vec<Value>,
    #[serde(default)]
    pub active_entities: Vec<Value>,
    #[serde(default)]
    pub player: PlayerInfo,
    #[serde(default)]
    pub player_private: PlayerPrivate,
    #[serde(default)]
    pub world: World,
    #[serde(default)]
    pub relationships: Map<String, Value>,
    #[serde(default)]
    pub history: Vec<Value>,
    #[serde(default)]
    pub permissions: PermissionsState,
    #[serde(default)]
    pub worldline: Worldline,
    #[serde(default)]
    pub memory: Memory,
    #[serde(default)]
    pub turn: u64,
    #[serde(default = "default_true")]
    pub is_new: bool,
    /// ISO8601。旧代码用空串占位,这里跟随。
    #[serde(default)]
    pub created_at: String,
    /// `user_locked_fields` 等迁移期产生的辅助键、未来未知字段,统一吃进这里,
    /// 反序列化不丢、序列化原样回写。
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

fn default_schema_version() -> u64 {
    6
}

fn default_true() -> bool {
    true
}

impl Default for GameStateData {
    fn default() -> Self {
        Self {
            schema_version: default_schema_version(),
            ruleset: Ruleset::default(),
            player_character: PlayerCharacter::default(),
            scene: Scene::default(),
            encounter: Encounter::default(),
            dice_log: Vec::new(),
            active_entities: Vec::new(),
            player: PlayerInfo::default(),
            player_private: PlayerPrivate::default(),
            world: World::default(),
            relationships: Map::new(),
            history: Vec::new(),
            permissions: PermissionsState::default(),
            worldline: Worldline::default(),
            memory: Memory::default(),
            turn: 0,
            is_new: true,
            created_at: String::new(),
            extra: Map::new(),
        }
    }
}

// ===== ruleset ==================================================================

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct Ruleset {
    pub id: String,
    pub mode: String,
    pub public_label: String,
}

impl Default for Ruleset {
    fn default() -> Self {
        Self {
            id: "dnd5e".into(),
            mode: "5e_compatible".into(),
            public_label: "5E compatible / 五版规则兼容".into(),
        }
    }
}

// ===== player_character =========================================================

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct PlayerCharacter {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub level: u32,
    #[serde(default)]
    pub class_name: String,
    #[serde(default)]
    pub species: String,
    #[serde(default)]
    pub background: String,
    #[serde(default)]
    pub abilities: Map<String, Value>,
    #[serde(default)]
    pub proficiency_bonus: i32,
    #[serde(default)]
    pub skills: Map<String, Value>,
    #[serde(default)]
    pub saves: Map<String, Value>,
    #[serde(default)]
    pub max_hp: i32,
    #[serde(default)]
    pub hp: i32,
    #[serde(default)]
    pub ac: i32,
    #[serde(default)]
    pub inventory: Vec<Value>,
    #[serde(default)]
    pub conditions: Vec<Value>,
    #[serde(default)]
    pub features: Vec<Value>,
    #[serde(default)]
    pub weapons: Map<String, Value>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

// ===== scene ====================================================================

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct Scene {
    #[serde(default)]
    pub module_id: String,
    #[serde(default)]
    pub location_id: String,
    #[serde(default)]
    pub visited_rooms: Vec<Value>,
    #[serde(default)]
    pub exits: Vec<Value>,
    #[serde(default)]
    pub visible_clues: Vec<Value>,
    #[serde(default)]
    pub flags: Map<String, Value>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

// ===== encounter ================================================================

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct Encounter {
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub round: u64,
    #[serde(default)]
    pub turn_index: u64,
    #[serde(default)]
    pub initiative_order: Vec<Value>,
    #[serde(default)]
    pub combatants: Vec<Value>,
    #[serde(default)]
    pub encounter_id: String,
    #[serde(default)]
    pub log: Vec<Value>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

// ===== player / player_private ==================================================

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct PlayerInfo {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub background: String,
    #[serde(default)]
    pub current_location: String,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct PlayerPrivate {
    #[serde(default)]
    pub secrets: Vec<Value>,
    #[serde(default)]
    pub flags: Map<String, Value>,
    #[serde(default)]
    pub hidden_traits: Vec<Value>,
    #[serde(default)]
    pub story_intent: String,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

// ===== world / timeline =========================================================

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct World {
    #[serde(default)]
    pub time: String,
    #[serde(default)]
    pub timeline: TimelineState,
    #[serde(default)]
    pub known_events: Vec<Value>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct TimelineState {
    #[serde(default = "default_anchor_state")]
    pub anchor_state: String,
    #[serde(default)]
    pub current_label: String,
    #[serde(default)]
    pub current_phase: String,
    #[serde(default = "default_anchor_source")]
    pub anchor_source: String,
    #[serde(default)]
    pub anchor_turn: u64,
    #[serde(default)]
    pub pending_jump: Option<Value>,
    #[serde(default)]
    pub last_transition: Option<Value>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

fn default_anchor_state() -> String {
    "locked".into()
}
fn default_anchor_source() -> String {
    "initial".into()
}

impl Default for TimelineState {
    fn default() -> Self {
        Self {
            anchor_state: default_anchor_state(),
            current_label: String::new(),
            current_phase: String::new(),
            anchor_source: default_anchor_source(),
            anchor_turn: 0,
            pending_jump: None,
            last_transition: None,
            extra: Map::new(),
        }
    }
}

// ===== permissions / audit / pending ============================================

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct PermissionsState {
    #[serde(default = "default_permission_mode")]
    pub mode: String,
    #[serde(default)]
    pub pending_writes: Vec<PendingWrite>,
    #[serde(default)]
    pub pending_questions: Vec<Value>,
    #[serde(default)]
    pub audit_log: Vec<AuditEntry>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

fn default_permission_mode() -> String {
    "full_access".into()
}

impl Default for PermissionsState {
    fn default() -> Self {
        Self {
            mode: default_permission_mode(),
            pending_writes: Vec::new(),
            pending_questions: Vec::new(),
            audit_log: Vec::new(),
            extra: Map::new(),
        }
    }
}

/// 一次 `apply_op` 的审计条目。
///
/// 命中各闸门时 `blocked` 填闸门名(`hard_forbidden` / `rules_managed` /
/// `module_managed`);成功写入时 `op` + `value` + `mode` 填实际写入。
/// 字段顺序贴近旧 `json!({...})` 输出。
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct AuditEntry {
    #[serde(default)]
    pub ts: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub blocked: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hint: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub op: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<String>,
    #[serde(default)]
    pub turn: u64,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

impl AuditEntry {
    pub fn now_ts() -> String {
        Utc::now().to_rfc3339()
    }

    pub fn blocked(source: &str, path: &str, gate: &str, turn: u64) -> Self {
        Self {
            ts: Self::now_ts(),
            source: source.into(),
            path: path.into(),
            blocked: Some(gate.into()),
            hint: None,
            op: None,
            value: None,
            mode: None,
            turn,
            extra: Map::new(),
        }
    }

    pub fn blocked_with_hint(source: &str, path: &str, gate: &str, hint: &str, turn: u64) -> Self {
        let mut e = Self::blocked(source, path, gate, turn);
        e.hint = Some(hint.into());
        e
    }

    pub fn applied(source: &str, path: &str, op: &str, value: Value, mode: &str, turn: u64) -> Self {
        Self {
            ts: Self::now_ts(),
            source: source.into(),
            path: path.into(),
            blocked: None,
            hint: None,
            op: Some(op.into()),
            value: Some(value),
            mode: Some(mode.into()),
            turn,
            extra: Map::new(),
        }
    }
}

/// 写入待审条目(权限模式不放行 + 未 force 时落到 pending)。
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct PendingWrite {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub value: Value,
    #[serde(default)]
    pub op: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub turn: u64,
    #[serde(default)]
    pub from: Value,
    #[serde(default)]
    pub to: Value,
    #[serde(default)]
    pub reason: String,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

// ===== worldline ================================================================

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct Worldline {
    #[serde(default)]
    pub user_variables: Map<String, Value>,
    #[serde(default)]
    pub divergence_chapter: Option<u64>,
    #[serde(default = "default_worldline_constraints")]
    pub constraints: Vec<String>,
    #[serde(default)]
    pub last_projection: Option<Value>,
    #[serde(default)]
    pub pending_projection: Option<Value>,
    #[serde(default)]
    pub last_validation: WorldlineValidation,
    #[serde(default)]
    pub custom_ui: Map<String, Value>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

fn default_worldline_constraints() -> Vec<String> {
    vec![
        "用户变量优先级高于世界线推演。".into(),
        "世界线推演必须先满足玩家设定,再外推局势。".into(),
        "若推演与用户变量冲突,必须报告冲突,不得写回为事实。".into(),
    ]
}

impl Default for Worldline {
    fn default() -> Self {
        Self {
            user_variables: Map::new(),
            divergence_chapter: None,
            constraints: default_worldline_constraints(),
            last_projection: None,
            pending_projection: None,
            last_validation: WorldlineValidation::default(),
            custom_ui: Map::new(),
            extra: Map::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct WorldlineValidation {
    #[serde(default = "default_validation_status")]
    pub status: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub turn: u64,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

fn default_validation_status() -> String {
    "none".into()
}

impl Default for WorldlineValidation {
    fn default() -> Self {
        Self {
            status: default_validation_status(),
            message: String::new(),
            turn: 0,
            extra: Map::new(),
        }
    }
}

// ===== memory ===================================================================

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(feature = "ts-rs", ts(export, export_to = "../../../../frontend/src/types/rust/"))]
pub struct Memory {
    #[serde(default = "default_memory_mode")]
    pub mode: String,
    #[serde(default)]
    pub main_quest: String,
    #[serde(default)]
    pub current_objective: String,
    #[serde(default)]
    pub resources: Vec<Value>,
    #[serde(default)]
    pub abilities: Vec<Value>,
    #[serde(default)]
    pub facts: Vec<Value>,
    #[serde(default)]
    pub pinned: Vec<Value>,
    #[serde(default)]
    pub notes: Vec<Value>,
    #[serde(default)]
    pub items: Vec<Value>,
    #[serde(default)]
    pub last_retrieval: String,
    #[serde(default)]
    pub last_context: Map<String, Value>,
    #[serde(default)]
    pub last_context_agent: Map<String, Value>,
    #[serde(default)]
    pub last_structured_updates: Vec<Value>,
    #[serde(flatten, default)]
    #[cfg_attr(feature = "ts-rs", ts(skip))]
    pub extra: Map<String, Value>,
}

fn default_memory_mode() -> String {
    "normal".into()
}

impl Default for Memory {
    fn default() -> Self {
        Self {
            mode: default_memory_mode(),
            main_quest: String::new(),
            current_objective: String::new(),
            resources: Vec::new(),
            abilities: Vec::new(),
            facts: Vec::new(),
            pinned: Vec::new(),
            notes: Vec::new(),
            items: Vec::new(),
            last_retrieval: String::new(),
            last_context: Map::new(),
            last_context_agent: Map::new(),
            last_structured_updates: Vec::new(),
            extra: Map::new(),
        }
    }
}

// ===== helpers ==================================================================

/// 同形旧 `rpg-state::default_state()` 的 JSON。供回归测试与迁移期 fallback 用。
///
/// 注意:`created_at` 由调用方写入(原代码会在 `GameState::new` 里塞当下时间)。
pub fn default_data_json() -> Value {
    serde_json::to_value(GameStateData::default()).expect("GameStateData default serializes")
}

/// `created_at` 工具:返回当下时间的 RFC3339 字符串。
pub fn now_rfc3339() -> String {
    Utc::now().to_rfc3339()
}

// 让 chrono 类型在调用方需要时可用(避免循环导入)。
#[allow(dead_code)]
fn _ensure_chrono_used() -> DateTime<Utc> {
    Utc::now()
}

// ===== 单测 =====================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// 触发 ts-rs 导出(--features ts-rs 时生效)。
    #[cfg(feature = "ts-rs")]
    #[test]
    fn export_ts_types() {
        // ts-rs 在 #[ts(export)] 时会通过 inventory/ctor 机制在测试结束后自动写文件。
        // 空函数体即可触发所有已注册的导出。
    }

    /// 与旧 `rpg-state::default_state()` 字面常量同形。
    /// 这份字面量是唯一事实源 — 改这里 = 改 schema。
    fn legacy_default_state_json() -> Value {
        json!({
            "schema_version": 6,
            "ruleset": {
                "id": "dnd5e",
                "mode": "5e_compatible",
                "public_label": "5E compatible / 五版规则兼容"
            },
            "player_character": {
                "name": "",
                "level": 0,
                "class_name": "",
                "species": "",
                "background": "",
                "abilities": {},
                "proficiency_bonus": 0,
                "skills": {},
                "saves": {},
                "max_hp": 0,
                "hp": 0,
                "ac": 0,
                "inventory": [],
                "conditions": [],
                "features": [],
                "weapons": {}
            },
            "scene": {
                "module_id": "",
                "location_id": "",
                "visited_rooms": [],
                "exits": [],
                "visible_clues": [],
                "flags": {}
            },
            "encounter": {
                "active": false,
                "round": 0,
                "turn_index": 0,
                "initiative_order": [],
                "combatants": [],
                "encounter_id": "",
                "log": []
            },
            "dice_log": [],
            "active_entities": [],
            "player": {
                "name": "",
                "role": "",
                "background": "",
                "current_location": ""
            },
            "player_private": {
                "secrets": [],
                "flags": {},
                "hidden_traits": [],
                "story_intent": ""
            },
            "world": {
                "time": "",
                "timeline": {
                    "anchor_state": "locked",
                    "current_label": "",
                    "current_phase": "",
                    "anchor_source": "initial",
                    "anchor_turn": 0,
                    "pending_jump": null,
                    "last_transition": null
                },
                "known_events": []
            },
            "relationships": {},
            "history": [],
            "permissions": {
                "mode": "full_access",
                "pending_writes": [],
                "pending_questions": [],
                "audit_log": []
            },
            "worldline": {
                "user_variables": {},
                "divergence_chapter": null,
                "constraints": [
                    "用户变量优先级高于世界线推演。",
                    "世界线推演必须先满足玩家设定,再外推局势。",
                    "若推演与用户变量冲突,必须报告冲突,不得写回为事实。"
                ],
                "last_projection": null,
                "pending_projection": null,
                "last_validation": {
                    "status": "none",
                    "message": "",
                    "turn": 0
                },
                "custom_ui": {}
            },
            "memory": {
                "mode": "normal",
                "main_quest": "",
                "current_objective": "",
                "resources": [],
                "abilities": [],
                "facts": [],
                "pinned": [],
                "notes": [],
                "items": [],
                "last_retrieval": "",
                "last_context": {},
                "last_context_agent": {},
                "last_structured_updates": []
            },
            "turn": 0,
            "is_new": true,
            "created_at": ""
        })
    }

    /// typed default → JSON 必须等于旧字面 `default_state()`。
    /// 这是 schema 不漂移的硬约束。
    #[test]
    fn typed_default_matches_legacy_json() {
        let typed = serde_json::to_value(GameStateData::default()).unwrap();
        let legacy = legacy_default_state_json();
        assert_eq!(typed, legacy, "typed default 必须与旧 default_state() 字面一致");
    }

    /// 旧 JSON → typed → JSON 必须无损往返。
    #[test]
    fn legacy_json_round_trip_lossless() {
        let legacy = legacy_default_state_json();
        let typed: GameStateData = serde_json::from_value(legacy.clone()).unwrap();
        let back = serde_json::to_value(&typed).unwrap();
        assert_eq!(back, legacy);
    }

    /// 未知顶层字段必须吃进 `extra` 不丢。
    #[test]
    fn unknown_top_level_field_survives_round_trip() {
        let mut input = legacy_default_state_json();
        input.as_object_mut().unwrap().insert(
            "user_locked_fields".into(),
            json!({"player.name": true}),
        );
        let typed: GameStateData = serde_json::from_value(input.clone()).unwrap();
        assert_eq!(
            typed.extra.get("user_locked_fields"),
            Some(&json!({"player.name": true}))
        );
        let back = serde_json::to_value(&typed).unwrap();
        assert_eq!(back, input);
    }

    /// `AuditEntry::blocked` / `applied` 的 JSON 形态必须与旧 `json!({...})` 一致
    /// (字段顺序无所谓,内容必须相等)。
    #[test]
    fn audit_entry_json_matches_legacy_shape() {
        let blocked = AuditEntry {
            ts: "2026-01-01T00:00:00+00:00".into(),
            source: "gm".into(),
            path: "encounter.active".into(),
            blocked: Some("rules_managed".into()),
            hint: None,
            op: None,
            value: None,
            mode: None,
            turn: 3,
            extra: Map::new(),
        };
        let v = serde_json::to_value(&blocked).unwrap();
        assert_eq!(
            v,
            json!({
                "ts": "2026-01-01T00:00:00+00:00",
                "source": "gm",
                "path": "encounter.active",
                "blocked": "rules_managed",
                "turn": 3,
            })
        );

        let applied = AuditEntry::applied(
            "user",
            "player_character.hp",
            "set",
            json!(10),
            "full_access",
            5,
        );
        let v2 = serde_json::to_value(&applied).unwrap();
        assert_eq!(v2["op"], json!("set"));
        assert_eq!(v2["value"], json!(10));
        assert_eq!(v2["mode"], json!("full_access"));
        assert_eq!(v2["turn"], json!(5));
        assert!(v2.get("blocked").is_none(), "成功 audit 不应含 blocked 键");
    }

    /// `PendingWrite` 必填字段往返一致。
    #[test]
    fn pending_write_round_trip() {
        let p = PendingWrite {
            id: "pw-1".into(),
            path: "encounter.round".into(),
            value: json!(2),
            op: "set".into(),
            source: "gm".into(),
            turn: 3,
            from: json!(1),
            to: json!(2),
            reason: "未授权".into(),
            extra: Map::new(),
        };
        let v = serde_json::to_value(&p).unwrap();
        let back: PendingWrite = serde_json::from_value(v).unwrap();
        assert_eq!(back, p);
    }

    /// 子结构的未知字段也应保留(extra 工作)。
    #[test]
    fn subtree_unknown_field_survives() {
        let mut player = json!({
            "name": "",
            "role": "",
            "background": "",
            "current_location": "",
            "future_field": 42,
        });
        let typed: PlayerInfo =
            serde_json::from_value(player.clone()).expect("unknown field 应吃进 extra");
        assert_eq!(typed.extra.get("future_field"), Some(&json!(42)));
        // 删 future_field 后输出对齐
        let back = serde_json::to_value(&typed).unwrap();
        assert_eq!(back["future_field"], json!(42));
        player.as_object_mut().unwrap().remove("future_field");
        let mut back_no_extra = back.clone();
        back_no_extra.as_object_mut().unwrap().remove("future_field");
        assert_eq!(back_no_extra, player);
    }
}
