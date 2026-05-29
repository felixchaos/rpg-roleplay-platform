//! 通用 OpenAI-compat 客户端 —— 拉取 `/v1/models` 列表,落 [`ModelInfo`]。
//!
//! 设计原则:
//! - **不依赖某家具体 provider**:用 [`OpenAICompatConfig`] 注入 base_url / api_key / 扩展 header,
//!   单实例适配 OpenAI / DeepSeek / xAI / MiMo / Hunyuan / OpenRouter / 任意自托管中转站。
//! - **降级到静态**:`models_endpoint=None` 或 HTTP 错误时,可以走 `static_models_path` 兜底,
//!   保证前端 UI 永远拿得到模型列表。
//! - **能力字段不在 /models 返回中**:provider config 自带 `enrich` 钩子来填 capability/pricing。
//!   通用 client 只负责"id + display_name"基本字段。

use std::collections::HashMap;

use chrono::Utc;
use serde::Deserialize;

use crate::schema::{CatalogError, CatalogSource, ModelCapabilities, ModelInfo, ProviderId};

/// 单个 OpenAI-compat provider 的客户端配置。
///
/// 字段:
/// - `base_url`:e.g. `https://api.openai.com/v1` 或用户自填中转站。
/// - `api_key_env`:环境变量名,e.g. `"OPENAI_API_KEY"`。**不读 env**,只记录名字,
///   让上层 catalog/runtime 处控,便于测试注入。
/// - `extra_headers`:额外 header(部分 provider 需 `HTTP-Referer` / `X-Title` 等)。
/// - `models_endpoint`:GET 路径,e.g. `Some("/models")`;`None` 表示只用 static。
/// - `static_models_path`:`include_str!` 路径标识(实际 JSON 字符串由 provider config 持有)。
#[derive(Debug, Clone)]
pub struct OpenAICompatConfig {
    pub provider_id: ProviderId,
    pub base_url: String,
    pub api_key_env: &'static str,
    pub extra_headers: Vec<(String, String)>,
    pub models_endpoint: Option<String>,
    pub static_models_path: Option<&'static str>,
}

/// OpenAI 风格 `/models` 响应顶层 envelope。
#[derive(Debug, Deserialize)]
struct ModelsListResponse {
    #[serde(default)]
    data: Vec<RawModelEntry>,
}

/// `/models` 列表单条原始记录。字段对齐 OpenAI 公开 schema,缺字段不报错。
#[derive(Debug, Deserialize)]
struct RawModelEntry {
    id: String,
    #[serde(default)]
    #[allow(dead_code)]
    object: Option<String>,
    /// OpenRouter 等富 catalog 端点会返回 display name。
    #[serde(default)]
    name: Option<String>,
    /// OpenRouter 等富 catalog 端点会返回上下文长度。
    #[serde(default)]
    context_length: Option<u32>,
    /// 富 catalog 携带 pricing(OpenRouter):字符串美元 / 1M token。
    #[serde(default)]
    pricing: Option<RawPricing>,
    /// 富 catalog 携带能力提示(OpenRouter):e.g. `["chat", "tools", "vision"]`。
    #[serde(default)]
    supported_parameters: Option<Vec<String>>,
    /// OpenRouter 顶层 architecture 字段提示模态。
    #[serde(default)]
    architecture: Option<RawArchitecture>,
    /// 顶层 top_provider 字段(OpenRouter)。
    #[serde(default)]
    top_provider: Option<RawTopProvider>,
}

#[derive(Debug, Deserialize)]
struct RawPricing {
    /// 输入 token 价格,单位:USD / token。OpenRouter 返回字符串。
    #[serde(default)]
    prompt: Option<String>,
    #[serde(default)]
    completion: Option<String>,
    #[serde(default)]
    input_cache_read: Option<String>,
    #[serde(default)]
    input_cache_write: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawArchitecture {
    #[serde(default)]
    input_modalities: Option<Vec<String>>,
    #[serde(default)]
    output_modalities: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct RawTopProvider {
    #[serde(default)]
    max_completion_tokens: Option<u32>,
    #[serde(default)]
    context_length: Option<u32>,
}

/// 拉 `/models`。返回的 `ModelInfo` 仅含基础字段;capability/pricing 已尽力从富响应解析,
/// 上层 provider config 可在 [`crate::providers::ProviderRegistry`] 层做后续 enrich。
pub async fn fetch_models(
    client: &reqwest::Client,
    config: &OpenAICompatConfig,
    api_key: Option<&str>,
) -> Result<Vec<ModelInfo>, CatalogError> {
    let endpoint = config
        .models_endpoint
        .as_deref()
        .ok_or(CatalogError::NoLiveEndpoint {
            provider: config.provider_id,
        })?;
    let url = format!("{}{}", config.base_url.trim_end_matches('/'), endpoint);

    let mut req = client.get(&url);
    if let Some(key) = api_key {
        req = req.bearer_auth(key);
    }
    for (k, v) in &config.extra_headers {
        req = req.header(k.as_str(), v.as_str());
    }

    let resp = req.send().await?.error_for_status()?;
    let body: ModelsListResponse = resp.json().await?;

    let now = Utc::now();
    let source = if matches!(config.provider_id, ProviderId::OpenRouter) {
        CatalogSource::OpenRouterProxy
    } else {
        CatalogSource::LiveApi
    };

    let mut out = Vec::with_capacity(body.data.len());
    for raw in body.data {
        out.push(parse_raw(raw, config.provider_id, source, now));
    }
    Ok(out)
}

fn parse_raw(
    raw: RawModelEntry,
    provider: ProviderId,
    source: CatalogSource,
    now: chrono::DateTime<Utc>,
) -> ModelInfo {
    let display = raw.name.clone().unwrap_or_else(|| raw.id.clone());

    // 价格字符串单位 USD/token → 转 per-million USD。
    let to_per_million = |s: &Option<String>| -> Option<f64> {
        s.as_ref()
            .and_then(|x| x.parse::<f64>().ok())
            .map(|v| v * 1_000_000.0)
    };
    let (input_cost, output_cost, cache_write, cache_read) = match &raw.pricing {
        Some(p) => (
            to_per_million(&p.prompt),
            to_per_million(&p.completion),
            to_per_million(&p.input_cache_write),
            to_per_million(&p.input_cache_read),
        ),
        None => (None, None, None, None),
    };

    let mut caps = ModelCapabilities::default();
    if let Some(params) = &raw.supported_parameters {
        let set: HashMap<&str, bool> = params.iter().map(|s| (s.as_str(), true)).collect();
        if set.contains_key("tools") || set.contains_key("tool_choice") {
            caps.tools = true;
            caps.function_calling = true;
        }
        if set.contains_key("response_format") || set.contains_key("structured_outputs") {
            caps.structured_output = true;
        }
        if set.contains_key("reasoning") {
            caps.extended_thinking = true;
        }
        if set.contains_key("web_search_options") {
            caps.web_search = true;
        }
        // OpenAI-compat 默认都支持 streaming
        caps.streaming = true;
    } else {
        caps.streaming = true;
    }
    if let Some(arch) = &raw.architecture {
        if let Some(inputs) = &arch.input_modalities {
            for m in inputs {
                match m.as_str() {
                    "image" => caps.vision = true,
                    "audio" => caps.audio = true,
                    "file" => caps.pdf_input = true,
                    _ => {}
                }
            }
        }
        if let Some(outs) = &arch.output_modalities {
            for m in outs {
                if m == "audio" {
                    caps.audio = true;
                }
            }
        }
    }

    let context_window = raw
        .context_length
        .or_else(|| raw.top_provider.as_ref().and_then(|t| t.context_length));
    let max_output_tokens = raw
        .top_provider
        .as_ref()
        .and_then(|t| t.max_completion_tokens);

    ModelInfo {
        id: raw.id,
        provider,
        display_name: display,
        context_window,
        max_output_tokens,
        input_cost_per_million: input_cost,
        output_cost_per_million: output_cost,
        cache_write_cost_per_million: cache_write,
        cache_read_cost_per_million: cache_read,
        capabilities: caps,
        unsupported_params: Vec::new(),
        deprecated_at: None,
        retiring_at: None,
        source,
        last_updated: now,
    }
}

/// 静态 catalog JSON 直接反序列化为 `Vec<ModelInfo>`,标记 source=StaticCatalog。
pub fn load_static_catalog(
    raw_json: &str,
    provider: ProviderId,
    path_label: &str,
) -> Result<Vec<ModelInfo>, CatalogError> {
    let mut models: Vec<ModelInfo> = serde_json::from_str(raw_json).map_err(|e| {
        CatalogError::StaticCatalogParse {
            path: path_label.to_string(),
            reason: e.to_string(),
        }
    })?;
    let now = Utc::now();
    for m in &mut models {
        // 防御性:静态 JSON 里也声明了 provider,但以 config 指定的为准。
        m.provider = provider;
        m.source = CatalogSource::StaticCatalog;
        m.last_updated = now;
    }
    Ok(models)
}
