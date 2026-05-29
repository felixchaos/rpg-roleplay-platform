//! rpg-rules-bridge — state.data (&mut serde_json::Value) 与规则引擎桥接
//! 对应 Python: rpg/rules_bridge/

pub mod error;
pub mod combat;
pub mod checks;
pub mod consume;
pub mod intent;
pub mod suggest;

pub use error::BridgeError;
pub use combat::{CombatAction, CombatOutcome, apply_combat};
pub use checks::{perform_skill_check, perform_saving_throw, trap_check};
pub use consume::{consume_item_action, short_rest, parse_consume_intent, ConsumeIntent};
pub use intent::{classify_combat_intent, has_movement_intent, direction_to_exit};
pub use suggest::suggest_rule_actions;
