//! schemas.mcp — MCP server 管理与工具调用路由请求模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

/// upsert_mcp_server 直接消费整个 body,字段透传。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpServerRequest {
    #[serde(flatten)]
    pub extra: HashMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpServerEnabledRequest {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default = "default_enabled")]
    pub enabled: Option<bool>,
}

fn default_enabled() -> Option<bool> { Some(true) }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpServerDeleteRequest {
    #[serde(default)]
    pub id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpServerValidateRequest {
    #[serde(default)]
    pub id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpServerStartRequest {
    #[serde(default)]
    pub id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpServerStopRequest {
    #[serde(default)]
    pub id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpToolCallRequest {
    #[serde(default)]
    pub server_id: Option<String>,
    #[serde(default)]
    pub tool: Option<String>,
    pub arguments: Option<HashMap<String, Value>>,
    #[serde(default = "default_tool_timeout")]
    pub timeout: Option<i64>,
}

fn default_tool_timeout() -> Option<i64> { Some(30) }
