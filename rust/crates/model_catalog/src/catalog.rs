//! 多 provider catalog 聚合 + per-provider TTL 缓存。
//!
//! 设计点:
//! - 缓存键 = [`ProviderId`],值 = `(Vec<ModelInfo>, Instant)`,Instant 是入库时间。
//! - `list_provider` 查 cache → 过期 / 缺失则 `refresh`;`refresh` 走 live API,失败降级 static。
//! - `list_all` 顺序聚合所有 10 家 provider(6 OpenAI-compat + 4 native)。
//! - 用 `DashMap` 而非 `Mutex<HashMap>`,避免 list_all 时的全局锁。
//!
//! 失败 fallback 链(每个 provider):**live → static catalog → 空 vec**。
//! native client 没有 api key / SA 凭据时直接走 static,不报错。

use std::time::{Duration, Instant};

use dashmap::DashMap;

use crate::providers::{
    agent_platform, alibaba_dashscope, anthropic, deepseek, google_ai_studio, openai, openai_compat,
    openrouter, tencent_hunyuan, xai, xiaomi_mimo,
};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

/// 已接入 catalog 的 OpenAI-compat provider 列表。
pub const KNOWN_OPENAI_COMPAT_PROVIDERS: &[ProviderId] = &[
    ProviderId::OpenAI,
    ProviderId::OpenRouter,
    ProviderId::DeepSeek,
    ProviderId::XAi,
    ProviderId::XiaomiMimo,
    ProviderId::TencentHunyuan,
];

/// 已接入 catalog 的 native protocol provider 列表(Wave 11-B)。
pub const KNOWN_NATIVE_PROVIDERS: &[ProviderId] = &[
    ProviderId::Anthropic,
    ProviderId::GoogleAIStudio,
    ProviderId::AgentPlatform,
    ProviderId::AlibabaQwen,
];

/// 全 10 家 provider 顺序。
pub const KNOWN_ALL_PROVIDERS: &[ProviderId] = &[
    ProviderId::OpenAI,
    ProviderId::OpenRouter,
    ProviderId::DeepSeek,
    ProviderId::XAi,
    ProviderId::XiaomiMimo,
    ProviderId::TencentHunyuan,
    ProviderId::Anthropic,
    ProviderId::GoogleAIStudio,
    ProviderId::AgentPlatform,
    ProviderId::AlibabaQwen,
];

pub struct ModelCatalog {
    cache: DashMap<ProviderId, (Vec<ModelInfo>, Instant)>,
    ttl: Duration,
    http: reqwest::Client,
    /// 用户可改的 base_url 覆盖表(中转站)。
    overrides: DashMap<ProviderId, String>,
    /// 显式注入的 API key(优先级高于 env 读取)。
    api_keys: DashMap<ProviderId, String>,
}

impl Default for ModelCatalog {
    fn default() -> Self {
        Self::new(Duration::from_secs(5 * 60))
    }
}

impl ModelCatalog {
    pub fn new(ttl: Duration) -> Self {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .build()
            .expect("reqwest client build");
        Self {
            cache: DashMap::new(),
            ttl,
            http,
            overrides: DashMap::new(),
            api_keys: DashMap::new(),
        }
    }

    /// 测试 / 注入用:替换内部 HTTP client(让 mock server 接管)。
    pub fn with_http(mut self, http: reqwest::Client) -> Self {
        self.http = http;
        self
    }

    /// 中转站:用户为某 provider 指定自定义 base_url(覆盖默认值)。
    pub fn set_base_url_override(&self, provider: ProviderId, base_url: String) {
        self.overrides.insert(provider, base_url);
    }

    /// 显式注入 provider 的 API key(测试 / 非 env 模式)。
    pub fn set_api_key(&self, provider: ProviderId, api_key: String) {
        self.api_keys.insert(provider, api_key);
    }

    /// 聚合所有已知 provider 的最新 catalog。失败的 provider 跳过(降级 static)。
    pub async fn list_all(&self) -> Vec<ModelInfo> {
        let mut all = Vec::new();
        for &p in KNOWN_ALL_PROVIDERS {
            match self.list_provider(p).await {
                Ok(mut v) => all.append(&mut v),
                Err(e) => {
                    tracing::warn!(provider = ?p, error = %e, "catalog list_provider 失败,跳过");
                }
            }
        }
        all
    }

    /// 查 cache,过期 / 缺失则 refresh 再返回。
    pub async fn list_provider(
        &self,
        provider: ProviderId,
    ) -> Result<Vec<ModelInfo>, CatalogError> {
        if let Some(entry) = self.cache.get(&provider) {
            if entry.1.elapsed() < self.ttl {
                return Ok(entry.0.clone());
            }
        }
        self.refresh(provider).await?;
        self.cache
            .get(&provider)
            .map(|e| e.0.clone())
            .ok_or(CatalogError::NoLiveEndpoint { provider })
    }

    /// 强制 refresh:走 live API → 失败降级 static catalog。
    pub async fn refresh(&self, provider: ProviderId) -> Result<(), CatalogError> {
        let models = if KNOWN_OPENAI_COMPAT_PROVIDERS.contains(&provider) {
            self.refresh_openai_compat(provider).await
        } else {
            self.refresh_native(provider).await
        };

        let models = models.unwrap_or_else(|e| {
            tracing::warn!(provider = ?provider, error = %e, "refresh 失败,降级空 vec");
            Vec::new()
        });
        self.cache.insert(provider, (models, Instant::now()));
        Ok(())
    }

    async fn refresh_openai_compat(
        &self,
        provider: ProviderId,
    ) -> Result<Vec<ModelInfo>, CatalogError> {
        let mut cfg = match provider {
            ProviderId::OpenAI => openai::config(),
            ProviderId::OpenRouter => openrouter::config(),
            ProviderId::DeepSeek => deepseek::config(),
            ProviderId::XAi => xai::config(),
            ProviderId::XiaomiMimo => xiaomi_mimo::config(),
            ProviderId::TencentHunyuan => tencent_hunyuan::config(),
            other => {
                return Err(CatalogError::InvalidConfig {
                    provider: other,
                    reason: format!("非 OpenAI-compat: {:?}", other),
                })
            }
        };

        if let Some(custom) = self.overrides.get(&provider) {
            cfg.base_url = custom.clone();
        }

        let live_result = if cfg.models_endpoint.is_some() {
            openai_compat::fetch_models(&self.http, &cfg, None).await
        } else {
            Err(CatalogError::NoLiveEndpoint { provider })
        };

        match live_result {
            Ok(v) if !v.is_empty() => Ok(v),
            Ok(_) => static_for(provider),
            Err(e) => {
                tracing::warn!(provider = ?provider, error = %e, "live /models 失败,降级 static");
                static_for(provider)
            }
        }
    }

    /// 4 个 native provider 的 refresh:走自家 client → 失败降级 static。
    async fn refresh_native(&self, provider: ProviderId) -> Result<Vec<ModelInfo>, CatalogError> {
        let base_override = self.overrides.get(&provider).map(|e| e.clone());
        let api_key = self.resolve_api_key(provider);

        let live_result = match provider {
            ProviderId::Anthropic => {
                anthropic::fetch_models(&self.http, api_key.as_deref(), base_override.as_deref())
                    .await
            }
            ProviderId::GoogleAIStudio => {
                google_ai_studio::fetch_models(
                    &self.http,
                    api_key.as_deref(),
                    base_override.as_deref(),
                )
                .await
            }
            ProviderId::AgentPlatform => {
                // Agent Platform 需 SA JSON,不通过 api_key 路径。
                match agent_platform::AgentPlatformConfig::from_env("google") {
                    Ok(mut cfg) => {
                        if let Some(b) = base_override.clone() {
                            cfg.base_url_override = Some(b);
                        }
                        agent_platform::fetch_models(&self.http, &cfg).await
                    }
                    Err(e) => Err(e),
                }
            }
            ProviderId::AlibabaQwen => {
                alibaba_dashscope::fetch_models(&self.http, api_key.as_deref()).await
            }
            other => Err(CatalogError::InvalidConfig {
                provider: other,
                reason: format!("非 native: {:?}", other),
            }),
        };

        match live_result {
            Ok(v) if !v.is_empty() => Ok(v),
            Ok(_) => static_for(provider),
            Err(e) => {
                tracing::warn!(provider = ?provider, error = %e, "native /models 失败,降级 static");
                static_for(provider)
            }
        }
    }

    /// 优先用 `set_api_key` 注入值;否则读 env。
    fn resolve_api_key(&self, provider: ProviderId) -> Option<String> {
        if let Some(v) = self.api_keys.get(&provider) {
            return Some(v.clone());
        }
        let env = match provider {
            ProviderId::Anthropic => anthropic::API_KEY_ENV,
            ProviderId::GoogleAIStudio => google_ai_studio::API_KEY_ENV,
            ProviderId::AlibabaQwen => alibaba_dashscope::API_KEY_ENV,
            // OpenAI-compat 走自身 config.api_key_env(但 /models 多数允许匿名)。
            ProviderId::OpenAI => openai::config().api_key_env,
            ProviderId::OpenRouter => openrouter::config().api_key_env,
            ProviderId::DeepSeek => deepseek::config().api_key_env,
            ProviderId::XAi => xai::config().api_key_env,
            ProviderId::XiaomiMimo => xiaomi_mimo::config().api_key_env,
            ProviderId::TencentHunyuan => tencent_hunyuan::config().api_key_env,
            ProviderId::AgentPlatform => return None, // SA JSON,不走 api_key 路径
        };
        std::env::var(env).ok()
    }

    /// 按模型 id 查找(扫所有 provider 的 cache;不命中返回 None)。
    pub fn get(&self, model_id: &str) -> Option<ModelInfo> {
        for entry in self.cache.iter() {
            if let Some(m) = entry.value().0.iter().find(|m| m.id == model_id) {
                return Some(m.clone());
            }
        }
        None
    }

    /// 同步预加载所有 provider 的 static catalog(供启动期或离线测试用)。
    pub fn preload_static(&self) -> Result<(), CatalogError> {
        for &p in KNOWN_ALL_PROVIDERS {
            let v = static_for(p)?;
            self.cache.insert(p, (v, Instant::now()));
        }
        Ok(())
    }
}

fn static_for(provider: ProviderId) -> Result<Vec<ModelInfo>, CatalogError> {
    match provider {
        ProviderId::OpenAI => openai::static_catalog(),
        ProviderId::OpenRouter => openrouter::static_catalog(),
        ProviderId::DeepSeek => deepseek::static_catalog(),
        ProviderId::XAi => xai::static_catalog(),
        ProviderId::XiaomiMimo => xiaomi_mimo::static_catalog(),
        ProviderId::TencentHunyuan => tencent_hunyuan::static_catalog(),
        ProviderId::Anthropic => anthropic::static_catalog(),
        ProviderId::GoogleAIStudio => google_ai_studio::static_catalog(),
        ProviderId::AgentPlatform => agent_platform::static_catalog(),
        ProviderId::AlibabaQwen => alibaba_dashscope::static_catalog(),
    }
}
