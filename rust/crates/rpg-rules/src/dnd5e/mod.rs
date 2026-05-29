//! dnd5e — 5E-compatible 规则集
//! 对应 Python: rpg/rules/dnd5e/

pub mod ruleset;
pub mod character;
pub mod checks;
pub mod combat;
pub mod monsters;
pub mod actions;

// ── base structs (对应 rpg/rules/base.py) ───────────────────────
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StateOp {
    pub op: String,
    pub path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<serde_json::Value>,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RuleResult {
    pub kind: String,
    pub actor: String,
    pub target: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub success: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dc: Option<i32>,
    pub roll: HashMap<String, serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub damage: Option<HashMap<String, serde_json::Value>>,
    pub state_ops: Vec<StateOp>,
    pub gm_facts: Vec<String>,
    pub extra: HashMap<String, serde_json::Value>,
}
