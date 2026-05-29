//! state.rs — GameState 主类型
//!
//! 对应 Python: `rpg/state/core.py::GameState` + `DEFAULT_STATE`。
//!
//! ## C3 后的设计(待办E 整树 typed 化)
//! - `data` 现在是 [`rpg_schemas::GameStateData`] typed struct,顶层 17 字段
//!   (含 11 个 typed struct + 4 个标量 + 4 个 Value 容器 + 1 个 `extra` flatten map)。
//! - 6 个 path API(`get_path` / `set_path` / `delete_path` / `append_to_path` /
//!   `inc_path` / `merge_path`)在 [`crate::typed_path`] 做 dispatch:按 head 段
//!   路由到 typed 字段,只 serialize 触达的子树(~µs),不动其他字段。
//! - 旧的 `data: Value + ensure_object_at` 模式全部退役;直接 typed 访问 +
//!   hot-path(`push_audit` / `push_pending`)走 [`typed_path::push_audit`] 直 typed push。
//! - `default_state() -> Value` 退化成 [`GameStateData::default`] 的 serde wrapper,保留
//!   旧外部签名(老的初始化 / 老测试可以继续用)。
//! - `version` + `updated_at` + `Arc<Value>` snapshot 缓存策略不变。
//!
//! ## `get_path` 返回类型变更
//! 原 `-> Option<&Value>` 改成 `-> Option<Value>`(owned)。typed 字段没有持久化的
//! `Value` 形态,无法返回引用。调用方此前几乎都接着 `.cloned()` 或 `.and_then(|v|
//! v.as_str())`,迁移成本极小。

use std::sync::Arc;

use chrono::{DateTime, Utc};
use rpg_schemas::GameStateData;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

use crate::path::PathError;
use crate::typed_path;

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
/// ## 字段
/// - `data: GameStateData` — typed 顶层(C3 之后,之前是 `Value`)。所有访问通过
///   typed 字段直读或 6 个 path API([`crate::typed_path`] 提供 dispatch)。
/// - `snapshot_cache: Arc<Value>` — 读快照缓存。首次调用 [`Self::snapshot`] 时把
///   `data` serialize 成 `Value` 缓存到 `Arc`,之后连续读只 `Arc::clone`。
///   [`Self::touch`] 写后清空缓存,保证一致性。`#[serde(skip)]` 且 `Clone` 不复制。
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct GameState {
    pub user_id: String,
    pub data: GameStateData,
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
    /// 创建空白存档。
    pub fn new(user_id: impl Into<String>) -> Self {
        let data = GameStateData {
            created_at: Utc::now().to_rfc3339(),
            ..GameStateData::default()
        };
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
    /// 旧存档 JSON 形态完全兼容:GameStateData 各字段都 `#[serde(default)]` +
    /// `#[serde(flatten)] extra: Map<String, Value>`,未知/缺失字段不会反序列化失败。
    ///
    /// TODO[Opus]: 完整迁移 `_migrate` 升级链。当前只兜底 schema_version。
    pub fn from_value(user_id: impl Into<String>, value: Value) -> Self {
        let mut data: GameStateData = if value.is_object() {
            serde_json::from_value(value).unwrap_or_default()
        } else {
            GameStateData::default()
        };
        data.schema_version = CURRENT_SCHEMA_VERSION;
        Self {
            user_id: user_id.into(),
            data,
            version: 0,
            updated_at: Utc::now(),
            snapshot_cache: parking_lot::Mutex::new(None),
        }
    }

    /// 返回 `data` 的 `Arc<Value>` 快照(读热路径用,避免每次 serialize 整树)。
    ///
    /// 首次调用(或写入后缓存失效)serialize 一次 `data` 构建 `Arc<Value>`;之后在
    /// 没有写入的情况下连续调用只 `Arc::clone`(仅自增引用计数)。配合 routes 层
    /// SSE / 状态返回等高频读不再每次 serialize。
    pub fn snapshot(&self) -> Arc<Value> {
        let mut guard = self.snapshot_cache.lock();
        if let Some(arc) = guard.as_ref() {
            return Arc::clone(arc);
        }
        let v = serde_json::to_value(&self.data).unwrap_or(Value::Null);
        let arc = Arc::new(v);
        *guard = Some(Arc::clone(&arc));
        arc
    }

    /// 读取 dot-path,不存在返回 None。返回 owned `Value`(typed 字段没有持久化
    /// `Value` 形态,无法返回引用)。
    pub fn get_path(&self, pointer: &str) -> Option<Value> {
        let cleaned = crate::path::clean_path(pointer);
        typed_path::get_path(&self.data, &cleaned)
    }

    /// 直接路径写入(不走 op 校验,内部 / rules_engine 专用)。
    /// 走业务规则请用 [`crate::ops::apply_op`]。
    pub fn set_path(&mut self, pointer: &str, value: Value) -> Result<(), StateError> {
        let cleaned = crate::path::clean_path(pointer);
        typed_path::set_path(&mut self.data, &cleaned, value)?;
        self.touch();
        Ok(())
    }

    pub fn delete_path(&mut self, pointer: &str) -> Result<Option<Value>, StateError> {
        let cleaned = crate::path::clean_path(pointer);
        let removed = typed_path::delete_path(&mut self.data, &cleaned)?;
        if removed.is_some() {
            self.touch();
        }
        Ok(removed)
    }

    pub fn append_to_path(&mut self, pointer: &str, value: Value) -> Result<(), StateError> {
        let cleaned = crate::path::clean_path(pointer);
        typed_path::append_path(&mut self.data, &cleaned, value)?;
        self.touch();
        Ok(())
    }

    /// 已存在路径数值 += delta。不存在则当作 0。
    /// 内部用,op_inc 走这里。
    pub fn inc_path(&mut self, pointer: &str, delta: f64) -> Result<f64, StateError> {
        let cleaned = crate::path::clean_path(pointer);
        let current = typed_path::get_path(&self.data, &cleaned)
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let next = current + delta;
        let value = serde_json::Number::from_f64(next).map(Value::Number).ok_or(
            StateError::TypeMismatch {
                path: cleaned.clone(),
                hint: "delta produced non-finite number",
            },
        )?;
        typed_path::set_path(&mut self.data, &cleaned, value)?;
        self.touch();
        Ok(next)
    }

    /// shallow merge 到指定路径(必须是 Object)。
    pub fn merge_path(&mut self, pointer: &str, value: Value) -> Result<(), StateError> {
        let cleaned = crate::path::clean_path(pointer);
        let Value::Object(incoming) = value else {
            return Err(StateError::TypeMismatch {
                path: cleaned,
                hint: "merge value must be object",
            });
        };
        typed_path::merge_path(&mut self.data, &cleaned, incoming)?;
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
    /// `GameStateData.turn` 是 u64 但本 API 返回 i64 保持外部签名稳定。
    pub fn turn(&self) -> i64 {
        self.data.turn as i64
    }

    /// 权限模式;返回未归一的原始字符串。归一化用 [`crate::ops::normalize_permission_mode`]。
    pub fn permission_mode_raw(&self) -> &str {
        &self.data.permissions.mode
    }
}

/// 默认状态的 JSON 形态。C3 后只是 [`GameStateData::default`] 的 serde wrapper,
/// 保留外部签名兼容老调用方。**新代码请用 [`GameStateData::default`] 直接拿 typed 值。**
///
/// 旧 Python `rpg/state/core.py::DEFAULT_STATE` 等价。
pub fn default_state() -> Value {
    serde_json::to_value(GameStateData::default()).unwrap_or(Value::Null)
}
