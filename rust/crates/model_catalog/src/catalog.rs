//! 多 provider catalog 聚合 + per-provider TTL 缓存。
//!
//! 设计点:
//! - 缓存键 = [`ProviderId`],值 = `(Vec<ModelInfo>, Instant)`,Instant 是入库时间。
//! - `list_provider` 查 cache → 过期 / 缺失则 `refresh`;`refresh` 走 live API,失败降级 static。
//! - `list_all` 顺序聚合所有 6 家 OpenAI-compat provider。
//! - 用 `DashMap` 而非 `Mutex<HashMap>`,避免 list_all 时的全局锁。

use std::time::{Duration, Instant};

use dashmap::DashMap;

use crate::providers::{deepseek, openai, openai_compat, openrouter, tencent_hunyuan, xai, xiaomi_mimo};
use crate::schema::{CatalogError, ModelInfo, ProviderId};

/// 已接入 catalog 的 OpenAI-compat provider 列表(Wave 11-A 阶段)。
pub const KNOWN_OPENAI_COMPAT_PROVIDERS: &[ProviderId] = &[
    ProviderId::OpenAI,
    ProviderId::OpenRouter,
    ProviderId::DeepSeek,
    ProviderId::XAi,
    ProviderId::XiaomiMimo,
    ProviderId::TencentHunyuan,
];

pub struct ModelCatalog {
    cache: DashMap<ProviderId, (Vec<ModelInfo>, Instant)>,
    ttl: Duration,
    http: reqwest::Client,
    /// 用户可改的 base_url 覆盖表(中转站),Wave 11-B 接入 settings 后填充。
    overrides: DashMap<ProviderId, String>,
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

    /// 聚合所有已知 provider 的最新 catalog。失败的 provider 跳过(降级 static)。
    pub async fn list_all(&self) -> Vec<ModelInfo> {
        let mut all = Vec::new();
        for &p in KNOWN_OPENAI_COMPAT_PROVIDERS {
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
    /// 注意:不读 env api key(Wave 11-A 简化);live 调用允许匿名,鉴权失败也降级。
    pub async fn refresh(&self, provider: ProviderId) -> Result<(), CatalogError> {
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
                    reason: format!("Wave 11-A 暂未接入 OpenAI-compat: {:?}", other),
                })
            }
        };

        if let Some(custom) = self.overrides.get(&provider) {
            cfg.base_url = custom.clone();
        }

        // 优先 live,失败降级 static。
        let live_result = if cfg.models_endpoint.is_some() {
            openai_compat::fetch_models(&self.http, &cfg, None).await
        } else {
            Err(CatalogError::NoLiveEndpoint { provider })
        };

        let models = match live_result {
            Ok(v) if !v.is_empty() => v,
            Ok(_) => static_for(provider)?,
            Err(e) => {
                tracing::warn!(provider = ?provider, error = %e, "live /models 失败,降级 static");
                static_for(provider)?
            }
        };

        self.cache.insert(provider, (models, Instant::now()));
        Ok(())
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
        for &p in KNOWN_OPENAI_COMPAT_PROVIDERS {
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
        other => Err(CatalogError::InvalidConfig {
            provider: other,
            reason: "无 static catalog".to_string(),
        }),
    }
}
