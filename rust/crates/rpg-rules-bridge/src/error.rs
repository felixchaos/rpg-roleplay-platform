//! BridgeError — 统一错误类型

use thiserror::Error;

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("state 中缺少字段：{0}")]
    MissingField(String),

    #[error("目标未找到：{0}")]
    TargetNotFound(String),

    #[error("战斗未激活")]
    EncounterNotActive,

    #[error("骰子错误：{0}")]
    Dice(#[from] rpg_rules::dice::DiceError),

    #[error("JSON 错误：{0}")]
    Json(#[from] serde_json::Error),

    #[error("{0}")]
    Logic(String),
}
