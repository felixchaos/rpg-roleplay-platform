//! state.rs — GameState 主类型
//!
//! 对应 Python: `rpg/state/core.py::GameState` + `DEFAULT_STATE`。
//! 设计取舍:
//! - `data` 用 `serde_json::Value` 持有顶层 Object,保留 Python 侧动态字段灵活性,
//!   迁移期不强行收紧 schema。后续具体子树需要强类型可单独抽到 `rpg-schemas`。
//! - `version` 每次成功 `apply_op` 自增,可用作乐观锁 / 客户端缓存失效。
//! - `updated_at` 每次写入刷新,对应 Python 的 `created_at` + 隐式 mtime。
//! - mixin 多继承(ApplyOpsMixin / PendingMixin / RulesGameplayMixin)在 Rust 侧
//!   全部平拍成单个 `impl GameState`。本文件只放数据 + 基础路径访问 + 默认模板;
//!   apply_op 主流程在 [`crate::ops`] 单独实现。

use std::sync::Arc;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;

use crate::path::{self, PathError};

pub const CURRENT_SCHEMA_VERSION: u64 = 6;

#[derive(Debug, Error)]
pub enum StateError {
    #[error("path error: {0}")]
    Path(#[from] PathError),
    #[error("type mismatch at {path}: {hint}")]
    TypeMismatch { path: String, hint: &'static str },
    #[error("serde error: {0}")]
    Serde(#[from] serde_json::Error),
}

/// 与 Python `GameState` 对齐的运行时存档。
///
/// 每个 user 一份;由 [`crate::store::StateStore`] 按 user_id 分片持有。
///
/// ## 读快照 Arc 化(6C-1)
/// `data` 仍是可变 `Value`(`set_path` 等写路径直接 `&mut self.data`);但 routes
/// 层高频的 `read().data.clone()` 会对整棵 JSON 树做深拷贝。为消除这条热路径上的
/// 深拷贝,本类型缓存一个 `Arc<Value>`(`snapshot_cache`):
///   - 每次写入(`touch`)使其失效(置 `None`);
///   - [`Self::snapshot`] 惰性重建一次,之后连续读只 `Arc::clone`(仅 inc refcount)。
///
/// 该字段是纯派生缓存,不参与序列化(`#[serde(skip)]`),`Clone` 时也不复制(置空,
/// 下次 `snapshot()` 自然重建),避免与 `data` 漂移。
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct GameState {
    pub user_id: String,
    pub data: Value,
    pub version: u64,
    pub updated_at: DateTime<Utc>,
    /// 派生快照缓存(见类型文档)。不序列化、不随 Clone 复制。
    #[serde(skip)]
    snapshot_cache: parking_lot::Mutex<Option<Arc<Value>>>,
}

impl Clone for GameState {
    fn clone(&self) -> Self {
        // 快照缓存是 data 的派生物,clone 时不复制(置空),下次 snapshot() 重建,
        // 杜绝克隆后缓存与各自 data 漂移。
        Self {
            user_id: self.user_id.clone(),
            data: self.data.clone(),
            version: self.version,
            updated_at: self.updated_at,
            snapshot_cache: parking_lot::Mutex::new(None),
        }
    }
}

impl GameState {
    /// 创建空白存档,DEFAULT_STATE 复刻 Python 模板。
    pub fn new(user_id: impl Into<String>) -> Self {
        let mut data = default_state();
        if let Some(obj) = data.as_object_mut() {
            obj.insert(
                "created_at".into(),
                Value::String(Utc::now().to_rfc3339()),
            );
        }
        Self {
            user_id: user_id.into(),
            data,
            version: 0,
            updated_at: Utc::now(),
            snapshot_cache: parking_lot::Mutex::new(None),
        }
    }

    /// 从已存在的 JSON 反序列化(对应 Python `load_or_new`)。
    ///
    /// TODO[Opus]: 完整迁移 `_migrate` 升级链。当前只做 schema_version 兜底,
    /// 长尾兼容(`memory.items` backfill / `player_private` 迁移 / timeline
    /// 默认值)留给后续 PR。
    pub fn from_value(user_id: impl Into<String>, mut data: Value) -> Self {
        if !data.is_object() {
            data = default_state();
        }
        if let Some(obj) = data.as_object_mut() {
            obj.insert(
                "schema_version".into(),
                Value::Number(CURRENT_SCHEMA_VERSION.into()),
            );
        }
        Self {
            user_id: user_id.into(),
            data,
            version: 0,
            updated_at: Utc::now(),
            snapshot_cache: parking_lot::Mutex::new(None),
        }
    }

    /// 返回 `data` 的 `Arc` 快照(读热路径用,避免整树深拷贝)。
    ///
    /// 首次调用(或写入后缓存失效)克隆一次 `data` 构建 `Arc<Value>`;之后在没有写入
    /// 的情况下连续调用只 `Arc::clone`(仅自增引用计数)。配合 routes 层把
    /// `read().data.clone()` 改为 `read().snapshot()`,SSE / 状态返回等高频读不再深拷贝。
    pub fn snapshot(&self) -> Arc<Value> {
        let mut guard = self.snapshot_cache.lock();
        if let Some(arc) = guard.as_ref() {
            return Arc::clone(arc);
        }
        let arc = Arc::new(self.data.clone());
        *guard = Some(Arc::clone(&arc));
        arc
    }

    /// 读取 dot-path,不存在返回 None。
    pub fn get_path(&self, pointer: &str) -> Option<&Value> {
        let cleaned = path::clean_path(pointer);
        path::get_path(&self.data, &cleaned)
    }

    /// 直接路径写入(不走 op 校验,内部 / rules_engine 专用)。
    /// 走业务规则请用 [`crate::ops::apply_op`]。
    pub fn set_path(&mut self, pointer: &str, value: Value) -> Result<(), StateError> {
        let cleaned = path::clean_path(pointer);
        path::set_path(&mut self.data, &cleaned, value)?;
        self.touch();
        Ok(())
    }

    pub fn delete_path(&mut self, pointer: &str) -> Result<Option<Value>, StateError> {
        let cleaned = path::clean_path(pointer);
        let removed = path::delete_path(&mut self.data, &cleaned)?;
        if removed.is_some() {
            self.touch();
        }
        Ok(removed)
    }

    pub fn append_to_path(&mut self, pointer: &str, value: Value) -> Result<(), StateError> {
        let cleaned = path::clean_path(pointer);
        path::append_path(&mut self.data, &cleaned, value)?;
        self.touch();
        Ok(())
    }

    /// 已存在路径数值 += delta。不存在则当作 0。
    /// 内部用,op_inc 走这里。
    pub fn inc_path(&mut self, pointer: &str, delta: f64) -> Result<f64, StateError> {
        let cleaned = path::clean_path(pointer);
        let current = path::get_path(&self.data, &cleaned)
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let next = current + delta;
        let value = serde_json::Number::from_f64(next).map(Value::Number).ok_or(
            StateError::TypeMismatch {
                path: cleaned.clone(),
                hint: "delta produced non-finite number",
            },
        )?;
        path::set_path(&mut self.data, &cleaned, value)?;
        self.touch();
        Ok(next)
    }

    /// shallow merge 到指定路径(必须是 Object)。
    pub fn merge_path(&mut self, pointer: &str, value: Value) -> Result<(), StateError> {
        let cleaned = path::clean_path(pointer);
        let Value::Object(incoming) = value else {
            return Err(StateError::TypeMismatch {
                path: cleaned,
                hint: "merge value must be object",
            });
        };
        // 用临时 Object 兜底:目标不存在或不是 Object 就替换为新 Object。
        let existing = path::get_path(&self.data, &cleaned).cloned();
        let mut target = match existing {
            Some(Value::Object(m)) => m,
            _ => serde_json::Map::new(),
        };
        for (k, v) in incoming {
            target.insert(k, v);
        }
        path::set_path(&mut self.data, &cleaned, Value::Object(target))?;
        self.touch();
        Ok(())
    }

    /// version + updated_at 同步刷新。所有写入路径必须调用。
    ///
    /// 同时使 `snapshot_cache` 失效:下次 [`Self::snapshot`] 会重建,保证读快照
    /// 与最新 `data` 一致。
    pub(crate) fn touch(&mut self) {
        self.version += 1;
        self.updated_at = Utc::now();
        // &mut self 已独占,直接清空缓存(get_mut 无锁开销)。
        *self.snapshot_cache.get_mut() = None;
    }

    /// 当前 turn,Python 侧 `data.get("turn", 0)`。
    pub fn turn(&self) -> i64 {
        self.data
            .get("turn")
            .and_then(|v| v.as_i64())
            .unwrap_or(0)
    }

    /// 权限模式;返回未归一的原始字符串。归一化用 [`crate::ops::normalize_permission_mode`]。
    pub fn permission_mode_raw(&self) -> &str {
        self.data
            .get("permissions")
            .and_then(|v| v.get("mode"))
            .and_then(|v| v.as_str())
            .unwrap_or("full_access")
    }
}

/// DEFAULT_STATE — 对应 Python `rpg/state/core.py::DEFAULT_STATE`。
///
/// 字段同 Python 原版;翻译期保留:
/// - `ruleset` / `player_character` / `scene` / `encounter` / `dice_log` —
///   5E-compatible 框架字段,只能由 RulesEngine 写,前端面板按此结构渲染。
/// - `player_private` — task 138 玩家隐私 namespace,GM prompt 一律屏蔽。
/// - `worldline.constraints` — 翻译期保留中文,避免 round-trip 失字。
///
/// TODO[Opus]: 与 `rpg-schemas` 强类型对齐后,部分子树(player_character /
/// encounter)可以从 strongly-typed struct 反序列化进来,减少字符串 key 漂移。
pub fn default_state() -> Value {
    json!({
        "schema_version": CURRENT_SCHEMA_VERSION,
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
