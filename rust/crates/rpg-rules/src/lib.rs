//! rpg-rules — 确定性规则引擎
//! 对应 Python: rpg/rules/

pub mod dice;
pub mod dnd5e;
pub mod engine;
pub mod modules;

pub use engine::{get_engine, RulesEngine};
pub use modules::{list_modules, load_module, ModuleBundle};
