//! schemas.permissions — 权限/确认管理路由请求模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PermissionsRequest {
    #[serde(default = "default_perm_mode")]
    pub mode: Option<String>,
}

fn default_perm_mode() -> Option<String> { Some("full_access".to_string()) }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PendingWriteRequest {
    pub id: Option<Value>,
    pub index: Option<Value>,
    pub action: Option<String>,
    pub decision: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct QuestionClearRequest {
    pub id: Option<Value>,
    pub index: Option<Value>,
    pub choice: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DebugPendingQuestionRequest {
    pub text: Option<String>,
}
