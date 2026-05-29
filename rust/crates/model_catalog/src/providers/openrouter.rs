//! OpenRouter provider config — 真打 `/api/v1/models` 富 catalog(含 pricing/capabilities)。
//!
//! OpenRouter 同时是"中转站适配层":用户可改 `base_url` 指向自托管反代;
//! `extra_headers` 默认带 `HTTP-Referer` / `X-Title` 是 OpenRouter 强烈建议的字段,
//! 用于他们的 leaderboard / analytics。中转站可清空。

use crate::providers::openai_compat::{load_static_catalog, OpenAICompatConfig};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

pub const STATIC_JSON: &str = include_str!("../../data/openrouter_fallback.json");

pub fn config() -> OpenAICompatConfig {
    OpenAICompatConfig {
        provider_id: ProviderId::OpenRouter,
        base_url: "https://openrouter.ai/api/v1".to_string(),
        api_key_env: "OPENROUTER_API_KEY",
        extra_headers: vec![
            (
                "HTTP-Referer".to_string(),
                "https://github.com/local/rpg-rust".to_string(),
            ),
            ("X-Title".to_string(), "我蕾穆丽娜不爱你".to_string()),
        ],
        models_endpoint: Some("/models".to_string()),
        static_models_path: Some("data/openrouter_fallback.json"),
    }
}

pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(
        STATIC_JSON,
        ProviderId::OpenRouter,
        "data/openrouter_fallback.json",
    )
}
