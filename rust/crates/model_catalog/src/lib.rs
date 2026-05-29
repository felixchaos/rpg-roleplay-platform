//! model_catalog — 多 provider 模型 catalog(schema + OpenAI-compat client + 聚合 + 缓存)。
//!
//! 模块:
//! - [`schema`] — `ModelInfo` / `ModelCapabilities` / `ProviderId` / `CatalogSource` typed schema,
//!   通过 `--features ts-rs` 导出到 `frontend/src/types/rust/catalog/`。
//! - [`providers`] — 通用 OpenAI-compat client 以及 6 家 provider 配置 + 静态 catalog。
//! - [`catalog`] — `ModelCatalog` 顶层聚合 + per-provider TTL 缓存。
//!
//! 公开重导出 typed schema + 顶层入口。

pub mod catalog;
pub mod providers;
pub mod schema;

pub use catalog::{
    ModelCatalog, KNOWN_ALL_PROVIDERS, KNOWN_NATIVE_PROVIDERS, KNOWN_OPENAI_COMPAT_PROVIDERS,
};
pub use providers::openai_compat::{fetch_models, load_static_catalog, OpenAICompatConfig};
pub use schema::{
    CatalogError, CatalogSource, ModelCapabilities, ModelInfo, ProviderId,
};
