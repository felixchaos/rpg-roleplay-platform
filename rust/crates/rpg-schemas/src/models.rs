//! schemas.models — 模型目录与 API 管理路由请求模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ModelsSelectRequest {
    #[serde(default)]
    pub api_id: Option<String>,
    #[serde(default)]
    pub model_id: Option<String>,
}

/// upsert_api 直接消费整个 body dict,字段透传。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ModelsUpsertApiRequest {
    #[serde(flatten)]
    pub extra: HashMap<String, Value>,
}

/// model 字段透传。允许前端直接发 flat payload (api_id + 各 model 字段)。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ModelsUpsertModelRequest {
    #[serde(default)]
    pub api_id: Option<String>,
    pub model: Option<HashMap<String, Value>>,
    #[serde(flatten)]
    pub extra: HashMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ModelsDeleteModelRequest {
    #[serde(default)]
    pub api_id: Option<String>,
    pub model_id: Option<String>,
    #[serde(default)]
    pub real_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ModelsProbeRequest {
    #[serde(default)]
    pub api_id: Option<String>,
    pub model: Option<String>,
    /// 默认 15 秒超时
    #[serde(default = "default_timeout")]
    pub timeout: Option<i64>,
}

fn default_timeout() -> Option<i64> { Some(15) }
