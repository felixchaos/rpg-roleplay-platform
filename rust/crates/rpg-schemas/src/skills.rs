//! schemas.skills — Skill 导入与运行路由请求模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SkillsImportRequest {
    pub file: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SkillRunRequest {
    pub cmd: Option<Vec<Value>>,
    pub command: Option<Vec<Value>>,
    pub stdin: Option<String>,
    pub timeout_sec: Option<i64>,
}
