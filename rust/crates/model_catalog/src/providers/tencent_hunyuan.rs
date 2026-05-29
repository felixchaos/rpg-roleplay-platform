//! 腾讯混元 provider config — OpenAI-compat 端点。
//!
//! Hunyuan OpenAI-compat 网关 = `https://api.hunyuan.cloud.tencent.com/v1`。

use crate::providers::openai_compat::{load_static_catalog, OpenAICompatConfig};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

pub const STATIC_JSON: &str = include_str!("../../data/tencent_hunyuan.json");

pub fn config() -> OpenAICompatConfig {
    OpenAICompatConfig {
        provider_id: ProviderId::TencentHunyuan,
        base_url: "https://api.hunyuan.cloud.tencent.com/v1".to_string(),
        api_key_env: "HUNYUAN_API_KEY",
        extra_headers: Vec::new(),
        models_endpoint: Some("/models".to_string()),
        static_models_path: Some("data/tencent_hunyuan.json"),
    }
}

pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(
        STATIC_JSON,
        ProviderId::TencentHunyuan,
        "data/tencent_hunyuan.json",
    )
}
