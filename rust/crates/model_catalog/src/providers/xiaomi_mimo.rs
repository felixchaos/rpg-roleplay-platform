//! 小米 MiMo provider config — OpenAI-compat 端点。
//!
//! MiMo 同时提供 OpenAI 协议(`/v1`)与 Anthropic 协议(`/anthropic/v1`)双端点;
//! 这里只接 OpenAI-compat 用于 catalog 拉取,实际 chat 推理 Wave 11-B 单独走 native client。
//!
//! base_url 选 OpenAI 协议;9 个 model id 来自上一波调研已 verified。

use crate::providers::openai_compat::{load_static_catalog, OpenAICompatConfig};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

pub const STATIC_JSON: &str = include_str!("../../data/xiaomi_mimo.json");

pub fn config() -> OpenAICompatConfig {
    OpenAICompatConfig {
        provider_id: ProviderId::XiaomiMimo,
        // 注:实际公开 endpoint 见 https://api.mimo.xiaomi.com,Wave 11-B 真打前以官方文档为准。
        base_url: "https://api.mimo.xiaomi.com/v1".to_string(),
        api_key_env: "XIAOMI_MIMO_API_KEY",
        extra_headers: Vec::new(),
        // MiMo /models 暂未公开稳定;先 None,降级走 static。
        models_endpoint: None,
        static_models_path: Some("data/xiaomi_mimo.json"),
    }
}

pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(
        STATIC_JSON,
        ProviderId::XiaomiMimo,
        "data/xiaomi_mimo.json",
    )
}
