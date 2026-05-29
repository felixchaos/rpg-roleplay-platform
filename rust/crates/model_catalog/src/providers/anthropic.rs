//! Anthropic provider — `GET https://api.anthropic.com/v1/models` 真打 + 静态 catalog 兜底。
//!
//! 参考: <https://docs.anthropic.com/en/api/models-list>
//!
//! 鉴权:
//! - `x-api-key: <ANTHROPIC_API_KEY>`
//! - `anthropic-version: 2023-06-01`
//!
//! 响应 schema (摘):
//! ```json
//! {
//!   "data": [
//!     {
//!       "id": "claude-sonnet-4-5",
//!       "type": "model",
//!       "display_name": "Claude Sonnet 4.5",
//!       "created_at": "2025-09-22T00:00:00Z",
//!       "deprecated_at": null,
//!       "retiring_at": null,
//!       "capabilities": {
//!         "thinking": true, "batch": true, "structured_outputs": true,
//!         "image_input": true, "pdf_input": true, "tool_use": true,
//!         "prompt_caching": true, "web_search": true
//!       }
//!     }
//!   ],
//!   "has_more": false,
//!   "first_id": "...",
//!   "last_id": "..."
//! }
//! ```
//!
//! cursor 分页: query `after_id=<last_id>` / `before_id=<first_id>`,本实现一次扫到 `has_more=false`。

use chrono::{DateTime, NaiveDate, Utc};
use serde::Deserialize;

use crate::providers::openai_compat::load_static_catalog;
use crate::schema::{
    CatalogError, CatalogSource, ModelCapabilities, ModelInfo, ProviderId,
};

pub const STATIC_JSON: &str = include_str!("../../data/anthropic.json");
pub const BASE_URL: &str = "https://api.anthropic.com/v1";
pub const API_VERSION: &str = "2023-06-01";
pub const API_KEY_ENV: &str = "ANTHROPIC_API_KEY";

/// 静态 catalog 兜底。
pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(STATIC_JSON, ProviderId::Anthropic, "data/anthropic.json")
}

#[derive(Debug, Deserialize)]
struct ModelsPage {
    #[serde(default)]
    data: Vec<RawAnthropicModel>,
    #[serde(default)]
    has_more: bool,
    #[serde(default)]
    #[allow(dead_code)]
    first_id: Option<String>,
    #[serde(default)]
    last_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawAnthropicModel {
    id: String,
    #[serde(default)]
    display_name: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    created_at: Option<String>,
    #[serde(default)]
    deprecated_at: Option<String>,
    #[serde(default)]
    retiring_at: Option<String>,
    #[serde(default)]
    capabilities: Option<RawAnthropicCaps>,
}

#[derive(Debug, Deserialize, Default)]
struct RawAnthropicCaps {
    #[serde(default)]
    thinking: bool,
    #[serde(default)]
    #[allow(dead_code)]
    batch: bool,
    #[serde(default)]
    structured_outputs: bool,
    #[serde(default)]
    image_input: bool,
    #[serde(default)]
    pdf_input: bool,
    #[serde(default)]
    tool_use: bool,
    #[serde(default)]
    prompt_caching: bool,
    #[serde(default)]
    web_search: bool,
}

/// 真打 `/v1/models`,自动 cursor 分页直到 `has_more=false`。
///
/// `api_key=None` → 直接报错(Anthropic /models 必须鉴权)。
/// `base_url_override`: 测试 / 中转站。
pub async fn fetch_models(
    client: &reqwest::Client,
    api_key: Option<&str>,
    base_url_override: Option<&str>,
) -> Result<Vec<ModelInfo>, CatalogError> {
    let key = api_key.ok_or(CatalogError::MissingApiKey {
        provider: ProviderId::Anthropic,
        env: API_KEY_ENV,
    })?;
    let base = base_url_override.unwrap_or(BASE_URL).trim_end_matches('/');

    let now = Utc::now();
    let mut out: Vec<ModelInfo> = Vec::new();
    let mut after_id: Option<String> = None;
    // 保险阀:正常 < 5 页,设 20 防死循环。
    for _ in 0..20 {
        let mut url = format!("{}/models?limit=100", base);
        if let Some(a) = &after_id {
            url.push_str(&format!("&after_id={}", a));
        }
        let resp = client
            .get(&url)
            .header("x-api-key", key)
            .header("anthropic-version", API_VERSION)
            .send()
            .await?
            .error_for_status()?;
        let page: ModelsPage = resp.json().await?;
        for raw in page.data {
            out.push(parse_anthropic(raw, now));
        }
        if !page.has_more || page.last_id.is_none() {
            break;
        }
        after_id = page.last_id;
    }
    Ok(out)
}

fn parse_anthropic(raw: RawAnthropicModel, now: DateTime<Utc>) -> ModelInfo {
    let display = raw.display_name.clone().unwrap_or_else(|| raw.id.clone());
    let caps_raw = raw.capabilities.unwrap_or_default();
    let capabilities = ModelCapabilities {
        streaming: true,
        tools: caps_raw.tool_use,
        vision: caps_raw.image_input,
        audio: false,
        structured_output: caps_raw.structured_outputs,
        extended_thinking: caps_raw.thinking,
        embedding: false,
        function_calling: caps_raw.tool_use,
        prompt_caching: caps_raw.prompt_caching,
        web_search: caps_raw.web_search,
        pdf_input: caps_raw.pdf_input,
    };
    ModelInfo {
        id: raw.id,
        provider: ProviderId::Anthropic,
        display_name: display,
        context_window: None,
        max_output_tokens: None,
        input_cost_per_million: None,
        output_cost_per_million: None,
        cache_write_cost_per_million: None,
        cache_read_cost_per_million: None,
        capabilities,
        unsupported_params: Vec::new(),
        deprecated_at: parse_date(&raw.deprecated_at),
        retiring_at: parse_date(&raw.retiring_at),
        source: CatalogSource::LiveApi,
        last_updated: now,
    }
}

/// 兼容 ISO date / RFC3339 两种格式,失败返回 None。
fn parse_date(s: &Option<String>) -> Option<NaiveDate> {
    let s = s.as_ref()?;
    if let Ok(d) = NaiveDate::parse_from_str(s, "%Y-%m-%d") {
        return Some(d);
    }
    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return Some(dt.naive_utc().date());
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_date_iso_and_rfc3339() {
        assert!(parse_date(&Some("2026-06-15".to_string())).is_some());
        assert!(parse_date(&Some("2026-06-15T00:00:00Z".to_string())).is_some());
        assert!(parse_date(&None).is_none());
        assert!(parse_date(&Some("garbage".to_string())).is_none());
    }
}
