//! rpg-rules — 确定性规则引擎
//! 对应 Python: rpg/rules/

pub mod dice;
pub mod dnd5e;
pub mod engine;

pub use engine::{get_engine, RulesEngine};
