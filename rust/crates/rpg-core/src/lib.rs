//! rpg-core — 配置 / 日志 / 错误 / 启动桩
//! 对应 Python: rpg/core/{config,logging,security,startup}.py

pub mod config;
pub mod error;
pub mod ids;
pub mod logging;
pub mod security;
pub mod startup;

pub use error::CoreError;
pub use ids::{RunId, SaveId, UserId};
