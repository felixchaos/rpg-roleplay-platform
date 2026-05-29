//! xAI provider config — `https://api.x.ai/v1` OpenAI-compat。

use crate::providers::openai_compat::{load_static_catalog, OpenAICompatConfig};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

pub const STATIC_JSON: &str = include_str!("../../data/xai.json");

pub fn config() -> OpenAICompatConfig {
    OpenAICompatConfig {
        provider_id: ProviderId::XAi,
        base_url: "https://api.x.ai/v1".to_string(),
        api_key_env: "XAI_API_KEY",
        extra_headers: Vec::new(),
        models_endpoint: Some("/models".to_string()),
        static_models_path: Some("data/xai.json"),
    }
}

pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(STATIC_JSON, ProviderId::XAi, "data/xai.json")
}
