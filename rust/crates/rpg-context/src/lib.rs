//! rpg-context — 上下文构建管线 + provider
//!
//! 对应 Python: rpg/context_engine/ + rpg/context_providers/
//!
//! 主入口:
//! - [`build_context_bundle`] — 组装单轮 GM prompt。
//! - [`run_providers`] — 按 manifest 顺序跑各 provider,返回 contributions。
//! - [`resolve_content_pack`] — 根据 state 推断 active manifest。
//!
//! 各 provider:
//! - `providers::memory::MemoryProvider` — 通用记忆
//! - `providers::recent_chat::RecentChatProvider` — 最近对话
//! - `providers::worldline::WorldlineProvider` — 用户硬约束变量
//! - `providers::rules::RulesProvider` — D&D 风格规则集状态
//! - `providers::module::ModuleSceneProvider` / `ModuleEncounterProvider` /
//!   `ModuleWorldbookProvider` — 模组路径
//! - `providers::novel::NovelTimelineProvider` / `NovelRetrievalProvider` /
//!   `NovelCharactersProvider` / `NovelWorldbookProvider` — 小说改编路径
//! - `providers::runtime_phase_digests::RuntimePhaseDigestProvider` — 长游戏历史摘要
//! - `providers::script_phase_anticipation::ScriptPhaseAnticipationProvider` — 剧本未来预期

pub mod engine;
pub mod error;
pub mod helpers;
pub mod layers;
pub mod provider;
pub mod providers;
pub mod registry;
pub mod rules_text;
pub mod types;
pub mod utils;

pub use engine::{build_context_bundle, format_history, recent_text};
pub use error::{ContextError, ContextResult};
pub use provider::{
    ContextProvider, ModuleLoaderFn, ProviderServices, RetrieveFn, TimelineFilterFn,
};
pub use registry::{
    available_providers, default_freeform_manifest, default_module_manifest,
    default_novel_manifest, get_provider, register_builtin_providers, register_provider,
    resolve_content_pack, run_providers,
};
pub use types::{ContextContribution, Demand, Layer, Manifest};
