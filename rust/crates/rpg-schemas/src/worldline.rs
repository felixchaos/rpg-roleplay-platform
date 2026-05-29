//! schemas.worldline — 世界线变量管理路由请求模型。
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct WorldlineVariableRequest {
    #[serde(default)]
    pub key: Option<String>,
    #[serde(default)]
    pub value: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct WorldlineVariableRemoveRequest {
    #[serde(default)]
    pub key: Option<String>,
}
