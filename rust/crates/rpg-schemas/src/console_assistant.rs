//! schemas.console_assistant — 侧栏控制台助手路由请求模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConsoleAssistantDeleteConversationRequest {
    #[serde(default)]
    pub conversation_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConsoleAssistantChatRequest {
    #[serde(default)]
    pub message: Option<String>,
    pub conversation_id: Option<String>,
    pub page_context: Option<HashMap<String, Value>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConsoleAssistantConfirmRequest {
    #[serde(default)]
    pub conversation_id: Option<String>,
    #[serde(default)]
    pub call_id: Option<String>,
    #[serde(default)]
    pub decision: Option<String>,
    pub page_context: Option<HashMap<String, Value>>,
}
