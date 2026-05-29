//! schemas.memory — 记忆管理路由请求模型。
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryModeRequest {
    #[serde(default = "default_mode")]
    pub mode: Option<String>,
}

fn default_mode() -> Option<String> { Some("normal".to_string()) }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryAddRequest {
    #[serde(default = "default_bucket")]
    pub bucket: Option<String>,
    #[serde(default)]
    pub text: Option<String>,
}

fn default_bucket() -> Option<String> { Some("notes".to_string()) }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryRemoveRequest {
    #[serde(default = "default_bucket_remove")]
    pub bucket: Option<String>,
    #[serde(default = "default_index")]
    pub index: Option<i64>,
}

fn default_bucket_remove() -> Option<String> { Some("notes".to_string()) }
fn default_index() -> Option<i64> { Some(-1) }
