//! OpenAI provider config — `/v1/models` 真打 + 静态 catalog 兜底。

use crate::providers::openai_compat::{load_static_catalog, OpenAICompatConfig};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

pub const STATIC_JSON: &str = include_str!("../../data/openai.json");

pub fn config() -> OpenAICompatConfig {
    OpenAICompatConfig {
        provider_id: ProviderId::OpenAI,
        base_url: "https://api.openai.com/v1".to_string(),
        api_key_env: "OPENAI_API_KEY",
        extra_headers: Vec::new(),
        models_endpoint: Some("/models".to_string()),
        static_models_path: Some("data/openai.json"),
    }
}

pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(STATIC_JSON, ProviderId::OpenAI, "data/openai.json")
}
