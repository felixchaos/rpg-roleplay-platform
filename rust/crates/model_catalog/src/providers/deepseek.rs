//! DeepSeek provider config — `https://api.deepseek.com` OpenAI-compat。
//!
//! 注:DeepSeek 官方 base URL 不含 `/v1`,但 `/models` / `/chat/completions` 都直挂根。

use crate::providers::openai_compat::{load_static_catalog, OpenAICompatConfig};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

pub const STATIC_JSON: &str = include_str!("../../data/deepseek.json");

pub fn config() -> OpenAICompatConfig {
    OpenAICompatConfig {
        provider_id: ProviderId::DeepSeek,
        base_url: "https://api.deepseek.com".to_string(),
        api_key_env: "DEEPSEEK_API_KEY",
        extra_headers: Vec::new(),
        models_endpoint: Some("/models".to_string()),
        static_models_path: Some("data/deepseek.json"),
    }
}

pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(STATIC_JSON, ProviderId::DeepSeek, "data/deepseek.json")
}
