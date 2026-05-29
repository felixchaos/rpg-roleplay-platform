//! schemas.game — 游戏核心流程路由请求模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct NewGameRequest {
    pub script_card_id: Option<Value>,
    pub script_id: Option<Value>,
    pub user_card_id: Option<Value>,
    pub persona_id: Option<Value>,
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default = "default_name")]
    pub name: Option<String>,
    #[serde(default)]
    pub background: Option<String>,
}

fn default_name() -> Option<String> { Some("无名者".to_string()) }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ChatEstimateRequest {
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default = "default_include_retrieval")]
    pub include_retrieval: Option<bool>,
}

fn default_include_retrieval() -> Option<bool> { Some(true) }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ChatRequest {
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub text: Option<String>,
    pub attachments: Option<Vec<Value>>,
}
