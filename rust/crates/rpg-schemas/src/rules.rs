//! schemas.rules — 5E 规则模组与战斗路由请求模型。
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RulesModuleStartRequest {
    #[serde(default = "default_module_id")]
    pub module_id: Option<String>,
    pub character: Option<Value>,
}

fn default_module_id() -> Option<String> { Some("ash_mine".to_string()) }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RulesModuleLaunchRequest {
    #[serde(default = "default_module_id_launch")]
    pub module_id: Option<String>,
    pub character: Option<Value>,
    #[serde(default)]
    pub title: Option<String>,
}

fn default_module_id_launch() -> Option<String> { Some("ash_mine".to_string()) }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RulesMoveRequest {
    #[serde(default)]
    pub to: Option<String>,
}

/// 通用动作,字段由 body.kind 决定,允许任意额外字段。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RulesActionRequest {
    pub kind: Option<String>,
    #[serde(flatten)]
    pub extra: HashMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RulesEncounterStartRequest {
    #[serde(default)]
    pub encounter_id: Option<String>,
    pub seed: Option<Value>,
}

/// pass — 无字段
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RulesEncounterNextRequest {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RulesEncounterEnemyRequest {
    #[serde(default)]
    pub attacker_id: Option<String>,
    #[serde(default = "default_target_id")]
    pub target_id: Option<String>,
    pub seed: Option<Value>,
}

fn default_target_id() -> Option<String> { Some("player".to_string()) }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RulesSuggestRequest {
    #[serde(default)]
    pub text: Option<String>,
}
