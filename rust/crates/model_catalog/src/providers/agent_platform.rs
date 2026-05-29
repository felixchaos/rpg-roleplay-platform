//! Agent Platform (formerly Vertex AI / Gemini Enterprise) provider —
//! `GET https://{region}-aiplatform.googleapis.com/v1beta1/publishers/{publisher}/models` 真打 + static catalog 兜底。
//!
//! display_name: "Agent Platform"
//! aliases: ["Vertex AI", "Gemini Enterprise"]
//!
//! 鉴权:service account JSON → OAuth2 access token (scope = `https://www.googleapis.com/auth/cloud-platform`),
//! 实现走 `yup-oauth2 ServiceAccountAuthenticator` (与 rpg-llm/vertex.rs 同源)。
//!
//! 配置来源:
//! - service account JSON 路径:`GOOGLE_APPLICATION_CREDENTIALS` env 或显式 [`AgentPlatformConfig`]。
//! - region:`VERTEX_REGION` env 或显式 (默认 `us-central1`)。
//! - publisher:`google` (Gemini) / `anthropic` (Claude on Agent Platform)。
//!
//! 响应字段 (摘):
//! ```json
//! {
//!   "publisherModels": [
//!     {
//!       "name": "publishers/google/models/gemini-2.5-pro",
//!       "versionId": "001",
//!       "openSourceCategory": "PROPRIETARY",
//!       "supportedActions": {...},
//!       "launchStage": "GA"
//!     }
//!   ]
//! }
//! ```

use std::path::PathBuf;

use chrono::Utc;
use serde::Deserialize;
use yup_oauth2::{ServiceAccountAuthenticator, ServiceAccountKey};

use crate::providers::openai_compat::load_static_catalog;
use crate::schema::{
    CatalogError, CatalogSource, ModelCapabilities, ModelInfo, ProviderId,
};

pub const STATIC_JSON: &str = include_str!("../../data/agent_platform.json");
pub const DEFAULT_REGION: &str = "us-central1";
pub const SA_PATH_ENV: &str = "GOOGLE_APPLICATION_CREDENTIALS";
pub const REGION_ENV: &str = "VERTEX_REGION";
pub const SCOPE: &str = "https://www.googleapis.com/auth/cloud-platform";
pub const DISPLAY_NAME: &str = "Agent Platform";
pub const ALIASES: &[&str] = &["Vertex AI", "Gemini Enterprise"];

/// Agent Platform 真打 catalog 所需的最小配置。
#[derive(Debug, Clone)]
pub struct AgentPlatformConfig {
    /// service account JSON 文件路径。
    pub service_account_path: PathBuf,
    /// 区域,默认 `us-central1`。
    pub region: String,
    /// publisher slug,e.g. `google` / `anthropic`。
    pub publisher: String,
    /// 测试 / 中转站:覆盖默认 `https://{region}-aiplatform.googleapis.com` 前缀。
    pub base_url_override: Option<String>,
}

impl AgentPlatformConfig {
    /// 从环境变量装载 (`GOOGLE_APPLICATION_CREDENTIALS` + `VERTEX_REGION`)。
    pub fn from_env(publisher: impl Into<String>) -> Result<Self, CatalogError> {
        let path = std::env::var(SA_PATH_ENV).map_err(|_| CatalogError::MissingApiKey {
            provider: ProviderId::AgentPlatform,
            env: SA_PATH_ENV,
        })?;
        let region = std::env::var(REGION_ENV).unwrap_or_else(|_| DEFAULT_REGION.to_string());
        Ok(Self {
            service_account_path: PathBuf::from(path),
            region,
            publisher: publisher.into(),
            base_url_override: None,
        })
    }
}

/// 静态 catalog 兜底。
pub fn static_catalog() -> Result<Vec<ModelInfo>, CatalogError> {
    load_static_catalog(
        STATIC_JSON,
        ProviderId::AgentPlatform,
        "data/agent_platform.json",
    )
}

#[derive(Debug, Deserialize)]
struct ModelsPage {
    #[serde(default, rename = "publisherModels")]
    publisher_models: Vec<RawPublisherModel>,
    #[serde(default, rename = "nextPageToken")]
    next_page_token: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawPublisherModel {
    /// "publishers/google/models/gemini-2.5-pro"
    name: String,
    #[serde(default, rename = "versionId")]
    #[allow(dead_code)]
    version_id: Option<String>,
    #[serde(default, rename = "openSourceCategory")]
    #[allow(dead_code)]
    open_source_category: Option<String>,
    #[serde(default, rename = "launchStage")]
    launch_stage: Option<String>,
}

/// 用 service account 鉴权,真打 catalog 端点。
///
/// 失败原因(常见):
/// - SA JSON 文件不存在 / 解析失败 → `CatalogError::InvalidConfig`
/// - OAuth2 token 获取失败 → `CatalogError::InvalidConfig`
/// - 网络层失败 → `CatalogError::Http`
pub async fn fetch_models(
    client: &reqwest::Client,
    config: &AgentPlatformConfig,
) -> Result<Vec<ModelInfo>, CatalogError> {
    let bytes = tokio::fs::read(&config.service_account_path)
        .await
        .map_err(|e| CatalogError::InvalidConfig {
            provider: ProviderId::AgentPlatform,
            reason: format!("读取 SA JSON 失败 ({}): {}", config.service_account_path.display(), e),
        })?;
    let key: ServiceAccountKey = serde_json::from_slice(&bytes).map_err(|e| {
        CatalogError::InvalidConfig {
            provider: ProviderId::AgentPlatform,
            reason: format!("SA JSON 解析失败: {}", e),
        }
    })?;
    let auth = ServiceAccountAuthenticator::builder(key)
        .build()
        .await
        .map_err(|e| CatalogError::InvalidConfig {
            provider: ProviderId::AgentPlatform,
            reason: format!("OAuth2 authenticator 构造失败: {}", e),
        })?;
    let token = auth
        .token(&[SCOPE])
        .await
        .map_err(|e| CatalogError::InvalidConfig {
            provider: ProviderId::AgentPlatform,
            reason: format!("OAuth2 token 获取失败: {}", e),
        })?;
    let access_token = token
        .token()
        .ok_or_else(|| CatalogError::InvalidConfig {
            provider: ProviderId::AgentPlatform,
            reason: "OAuth2 token 为空".to_string(),
        })?;

    let base = match &config.base_url_override {
        Some(s) => s.trim_end_matches('/').to_string(),
        None => format!("https://{}-aiplatform.googleapis.com", config.region),
    };

    let now = Utc::now();
    let mut out: Vec<ModelInfo> = Vec::new();
    let mut page_token: Option<String> = None;
    for _ in 0..20 {
        let mut url = format!(
            "{}/v1beta1/publishers/{}/models?pageSize=200",
            base, config.publisher
        );
        if let Some(t) = &page_token {
            url.push_str(&format!("&pageToken={}", t));
        }
        let resp = client
            .get(&url)
            .bearer_auth(access_token)
            .send()
            .await?
            .error_for_status()?;
        let page: ModelsPage = resp.json().await?;
        for raw in page.publisher_models {
            out.push(parse_publisher_model(raw, &config.publisher, now));
        }
        match page.next_page_token {
            Some(t) if !t.is_empty() => page_token = Some(t),
            _ => break,
        }
    }
    Ok(out)
}

fn parse_publisher_model(
    raw: RawPublisherModel,
    publisher: &str,
    now: chrono::DateTime<Utc>,
) -> ModelInfo {
    // "publishers/google/models/gemini-2.5-pro" → "gemini-2.5-pro"
    let id = raw
        .name
        .rsplit('/')
        .next()
        .unwrap_or(&raw.name)
        .to_string();
    let display = format!("{} ({})", id, publisher);

    // Agent Platform /models 不返回 capability 细节 — 用启发式:
    // Gemini 全默认支持 tools/vision/structured_output;Claude on AP 同 Anthropic。
    let caps = ModelCapabilities {
        streaming: true,
        tools: true,
        function_calling: true,
        structured_output: true,
        vision: matches!(publisher, "google" | "anthropic"),
        prompt_caching: publisher == "anthropic",
        ..ModelCapabilities::default()
    };

    // launch_stage = "DEPRECATED" 标记。
    let deprecated_at = if raw.launch_stage.as_deref() == Some("DEPRECATED") {
        Some(now.naive_utc().date())
    } else {
        None
    };

    ModelInfo {
        id,
        provider: ProviderId::AgentPlatform,
        display_name: display,
        context_window: None,
        max_output_tokens: None,
        input_cost_per_million: None,
        output_cost_per_million: None,
        cache_write_cost_per_million: None,
        cache_read_cost_per_million: None,
        capabilities: caps,
        unsupported_params: Vec::new(),
        deprecated_at,
        retiring_at: None,
        source: CatalogSource::LiveApi,
        last_updated: now,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_name_and_aliases_const() {
        assert_eq!(DISPLAY_NAME, "Agent Platform");
        assert!(ALIASES.contains(&"Vertex AI"));
        assert!(ALIASES.contains(&"Gemini Enterprise"));
    }

    #[test]
    fn from_env_reports_missing_sa() {
        std::env::remove_var(SA_PATH_ENV);
        let err = AgentPlatformConfig::from_env("google").unwrap_err();
        match err {
            CatalogError::MissingApiKey { env, .. } => assert_eq!(env, SA_PATH_ENV),
            other => panic!("unexpected: {:?}", other),
        }
    }
}
