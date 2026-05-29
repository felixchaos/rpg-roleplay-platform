//! schemas._common — 全局共享基础模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

/// 通用 ok 响应。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OkResponse {
    #[serde(default = "default_true")]
    pub ok: bool,
}

impl Default for OkResponse {
    fn default() -> Self {
        Self { ok: true }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorResponse {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub error: String,
}

impl Default for ErrorResponse {
    fn default() -> Self {
        Self { ok: false, error: String::new() }
    }
}

/// 通用 ok + state payload。state 字段允许任意嵌套。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateResponse {
    #[serde(default = "default_true")]
    pub ok: bool,
    pub state: Option<HashMap<String, Value>>,
    pub error: Option<String>,
}

/// 通用响应(ok + 任意附加字段)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenericOkResponse {
    #[serde(default = "default_true")]
    pub ok: bool,
    /// 额外字段透传
    #[serde(flatten)]
    pub extra: HashMap<String, Value>,
}

fn default_true() -> bool { true }
