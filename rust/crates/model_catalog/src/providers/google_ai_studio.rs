//! Google AI Studio (Gemini API) provider —
//! `GET https://generativelanguage.googleapis.com/v1beta/models` 真打 + static catalog 兜底。
//!
//! 鉴权:`x-goog-api-key` header (官方推荐) 或 `?key=` query param。
//! 注意:这是 Google AI Studio,**与** Agent Platform (Vertex AI) 不同 — 后者用 service account JSON。
//!
//! 响应 schema (摘):
//! ```json
//! {
//!   "models": [
//!     {
//!       "name": "models/gemini-2.5-pro",
//!       "version": "001",
//!       "displayName": "Gemini 2.5 Pro",
//!       "description": "...",
//!       "inputTokenLimit": 2097152,
//!       "outputTokenLimit": 65536,
//!       "supportedGenerationMethods": ["generateContent", "countTokens"],
//!       "thinking": true
//!     }
//!   ],
//!   "nextPageToken": "..."
//! }
//! ```
//!
//! 分页:`?pageToken=<next>`,每页默认 50 个。

use chrono::Utc;
use serde::Deserialize;

use crate::providers::openai_compat::load_static_catalog;
use crate::schema::{
    CatalogError, CatalogSource, ModelCapabilities, ModelInfo, ProviderId,
};

pub const STATIC_JSON: &str = include_str!("../../data/google_ai_studio.json");
pub const BASE_URL: &str = "https://generativelanguage.googleapis.com/v1beta";
pub const API_KEY_ENV: &str = "GOOGLE_AI_STUDIO_API_KEY";

pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(
        STATIC_JSON,
        ProviderId::GoogleAIStudio,
        "data/google_ai_studio.json",
    )
}

#[derive(Debug, Deserialize)]
struct ModelsPage {
    #[serde(default)]
    models: Vec<RawGoogleModel>,
    #[serde(default, rename = "nextPageToken")]
    next_page_token: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawGoogleModel {
    /// 形如 "models/gemini-2.5-pro"
    name: String,
    #[serde(default, rename = "displayName")]
    display_name: Option<String>,
    #[serde(default, rename = "inputTokenLimit")]
    input_token_limit: Option<u32>,
    #[serde(default, rename = "outputTokenLimit")]
    output_token_limit: Option<u32>,
    #[serde(default, rename = "supportedGenerationMethods")]
    supported_generation_methods: Option<Vec<String>>,
    #[serde(default)]
    thinking: Option<bool>,
}

/// 真打 `/v1beta/models`,自动 page token 分页。
pub async fn fetch_models(
    client: &reqwest::Client,
    api_key: Option<&str>,
    base_url_override: Option<&str>,
) -> Result<Vec<ModelInfo>, CatalogError> {
    let key = api_key.ok_or(CatalogError::MissingApiKey {
        provider: ProviderId::GoogleAIStudio,
        env: API_KEY_ENV,
    })?;
    let base = base_url_override.unwrap_or(BASE_URL).trim_end_matches('/');

    let now = Utc::now();
    let mut out: Vec<ModelInfo> = Vec::new();
    let mut page_token: Option<String> = None;
    for _ in 0..20 {
        let mut url = format!("{}/models?pageSize=100", base);
        if let Some(t) = &page_token {
            url.push_str(&format!("&pageToken={}", t));
        }
        let resp = client
            .get(&url)
            .header("x-goog-api-key", key)
            .send()
            .await?
            .error_for_status()?;
        let page: ModelsPage = resp.json().await?;
        for raw in page.models {
            out.push(parse_google(raw, now));
        }
        match page.next_page_token {
            Some(t) if !t.is_empty() => page_token = Some(t),
            _ => break,
        }
    }
    Ok(out)
}

fn parse_google(raw: RawGoogleModel, now: chrono::DateTime<Utc>) -> ModelInfo {
    // "models/gemini-2.5-pro" → "gemini-2.5-pro"
    let id = raw
        .name
        .strip_prefix("models/")
        .unwrap_or(&raw.name)
        .to_string();
    let display = raw.display_name.clone().unwrap_or_else(|| id.clone());

    let methods = raw.supported_generation_methods.unwrap_or_default();
    let supports_generate = methods.iter().any(|m| m == "generateContent");
    let supports_embed = methods
        .iter()
        .any(|m| m == "embedContent" || m == "batchEmbedContents");

    let mut caps = ModelCapabilities::default();
    if supports_generate {
        caps.streaming = true;
        // Gemini 全系默认支持 tools/vision/structured_output,粗粒度位由 catalog 维护。
        caps.tools = true;
        caps.function_calling = true;
        caps.structured_output = true;
    }
    if raw.thinking.unwrap_or(false) {
        caps.extended_thinking = true;
    }
    if supports_embed {
        caps.embedding = true;
    }

    ModelInfo {
        id,
        provider: ProviderId::GoogleAIStudio,
        display_name: display,
        context_window: raw.input_token_limit,
        max_output_tokens: raw.output_token_limit,
        input_cost_per_million: None,
        output_cost_per_million: None,
        cache_write_cost_per_million: None,
        cache_read_cost_per_million: None,
        capabilities: caps,
        unsupported_params: Vec::new(),
        deprecated_at: None,
        retiring_at: None,
        source: CatalogSource::LiveApi,
        last_updated: now,
    }
}
