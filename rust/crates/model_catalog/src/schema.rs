//! model_catalog 顶层 typed schema
//!
//! 与前端共享类型(via ts-rs)。所有结构体 / enum 都派生 `serde::{Serialize, Deserialize}`,
//! 通过 `--features ts-rs` 触发导出到 `frontend/src/types/rust/catalog/`。
//!
//! 设计要点:
//! - `ModelInfo` 字段尽量贴近常见 provider /models 端点格式,缺值用 `Option`。
//! - `capabilities` 是细粒度布尔位,而不是粗 enum,方便前端 UI 过滤。
//! - `unsupported_params` 与 LiteLLM `model_cost.json` 对齐。
//! - `CatalogSource` 标记 catalog 数据来源,用于 UI 显示"实时/缓存/用户改写"。

use chrono::{DateTime, NaiveDate, Utc};
use serde::{Deserialize, Serialize};

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

/// 单个模型的元数据(对应 /models 返回的一条 + capability/pricing 富信息)。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/catalog/")
)]
pub struct ModelInfo {
    pub id: String,
    pub provider: ProviderId,
    pub display_name: String,
    pub context_window: Option<u32>,
    pub max_output_tokens: Option<u32>,
    pub input_cost_per_million: Option<f64>,
    pub output_cost_per_million: Option<f64>,
    pub cache_write_cost_per_million: Option<f64>,
    pub cache_read_cost_per_million: Option<f64>,
    pub capabilities: ModelCapabilities,
    pub unsupported_params: Vec<String>,
    pub deprecated_at: Option<NaiveDate>,
    pub retiring_at: Option<NaiveDate>,
    pub source: CatalogSource,
    pub last_updated: DateTime<Utc>,
}

/// 模型能力 bit-set。所有字段默认 false,catalog 显式设 true。
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/catalog/")
)]
pub struct ModelCapabilities {
    pub streaming: bool,
    pub tools: bool,
    pub vision: bool,
    pub audio: bool,
    pub structured_output: bool,
    pub extended_thinking: bool,
    pub embedding: bool,
    pub function_calling: bool,
    pub prompt_caching: bool,
    pub web_search: bool,
    pub pdf_input: bool,
}

/// 已对接 provider 的标识。后续 Wave 加新 provider 在此追加。
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/catalog/")
)]
pub enum ProviderId {
    OpenAI,
    Anthropic,
    GoogleAIStudio,
    AgentPlatform,
    OpenRouter,
    DeepSeek,
    XAi,
    XiaomiMimo,
    AlibabaQwen,
    TencentHunyuan,
}

impl ProviderId {
    /// 用作 catalog cache key / UI slug 的 stable 字符串。
    pub fn slug(self) -> &'static str {
        match self {
            ProviderId::OpenAI => "openai",
            ProviderId::Anthropic => "anthropic",
            ProviderId::GoogleAIStudio => "google_ai_studio",
            ProviderId::AgentPlatform => "agent_platform",
            ProviderId::OpenRouter => "openrouter",
            ProviderId::DeepSeek => "deepseek",
            ProviderId::XAi => "xai",
            ProviderId::XiaomiMimo => "xiaomi_mimo",
            ProviderId::AlibabaQwen => "alibaba_qwen",
            ProviderId::TencentHunyuan => "tencent_hunyuan",
        }
    }
}

/// catalog 数据来源,前端 UI 用于显示"实时/缓存/用户改写"。
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/catalog/")
)]
pub enum CatalogSource {
    /// 实时从 provider /models 拉取。
    LiveApi,
    /// `include_str!` 内嵌静态 JSON。
    StaticCatalog,
    /// 用户配置覆盖(中转站 / 自托管)。
    UserOverride,
    /// OpenRouter 聚合 pricing(代理多家)。
    OpenRouterProxy,
}

/// catalog 操作过程中的错误。
#[derive(Debug, thiserror::Error)]
pub enum CatalogError {
    #[error("HTTP 请求失败: {0}")]
    Http(#[from] reqwest::Error),
    #[error("JSON 解析失败: {0}")]
    Json(#[from] serde_json::Error),
    #[error("provider {provider:?} 未实现实时 /models endpoint")]
    NoLiveEndpoint { provider: ProviderId },
    #[error("provider {provider:?} 配置无效: {reason}")]
    InvalidConfig {
        provider: ProviderId,
        reason: String,
    },
    #[error("provider {provider:?} API 鉴权失败: 环境变量 {env} 未设置")]
    MissingApiKey {
        provider: ProviderId,
        env: &'static str,
    },
    #[error("静态 catalog {path} 解析失败: {reason}")]
    StaticCatalogParse { path: String, reason: String },
}
