//! registry — 模型目录 + 路由层。
//!
//! 对应 Python `rpg/model_registry.py` + `rpg/model_probe.py`。
//! 目录格式与 Python 端 model_catalog.json 兼容(同样的 schema_version=1):
//!   { "selected": {"api_id":..., "model_id":...},
//!     "apis": [ { "id": "...", "kind": "...", "models":[...] }, ... ] }
//!
//! `LlmRouter` 持有一组按 BackendKind 索引的 `Arc<dyn LlmBackend>`,根据 catalog
//! 的 selected.api_id → kind 映射,选出 backend 来转发 stream_chat。

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::any_backend::AnyBackend;
use crate::pipeline::{BackendKind, ChunkStream, LlmBackend, LlmError, ModelInfo};
use crate::pipeline::{ChatRequest};

// -----------------------------------------------------------------------------
// ModelPricing — 每千 token 计价,USD
// -----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelPricing {
    pub input_per_1k_usd: f64,
    pub output_per_1k_usd: f64,
    pub cache_read_per_1k_usd: f64,
    pub cache_write_per_1k_usd: f64,
}

impl ModelPricing {
    /// 内置默认定价表 — key: `"{api_id}/{model_id}"`。
    /// 数据来源:各 provider 官网,按 2025-Q2 定价录入。
    fn builtin_table() -> HashMap<String, ModelPricing> {
        let mut m = HashMap::new();
        // ── Anthropic ──────────────────────────────────────────────────────
        // claude-opus-4-7
        m.insert("anthropic/claude-opus-4-7".into(), ModelPricing {
            input_per_1k_usd: 0.015,
            output_per_1k_usd: 0.075,
            cache_read_per_1k_usd: 0.0015,
            cache_write_per_1k_usd: 0.01875,
        });
        // claude-sonnet-4-6
        m.insert("anthropic/claude-sonnet-4-6".into(), ModelPricing {
            input_per_1k_usd: 0.003,
            output_per_1k_usd: 0.015,
            cache_read_per_1k_usd: 0.0003,
            cache_write_per_1k_usd: 0.00375,
        });
        // claude-haiku-4-5
        m.insert("anthropic/claude-haiku-4-5".into(), ModelPricing {
            input_per_1k_usd: 0.00025,
            output_per_1k_usd: 0.00125,
            cache_read_per_1k_usd: 0.00003,
            cache_write_per_1k_usd: 0.0003,
        });
        // ── Gemini (Vertex AI / Google AI Studio) ──────────────────────────
        // gemini-2.5-flash (≤200k ctx)
        m.insert("vertex_ai/gemini-2.5-flash".into(), ModelPricing {
            input_per_1k_usd: 0.000075,
            output_per_1k_usd: 0.0003,
            cache_read_per_1k_usd: 0.0000188,
            cache_write_per_1k_usd: 0.000075,
        });
        // gemini-2.5-pro
        m.insert("vertex_ai/gemini-2.5-pro".into(), ModelPricing {
            input_per_1k_usd: 0.00125,
            output_per_1k_usd: 0.01,
            cache_read_per_1k_usd: 0.0003125,
            cache_write_per_1k_usd: 0.00125,
        });
        // ── DeepSeek ───────────────────────────────────────────────────────
        // deepseek-v3 (openai_compat)
        m.insert("openai_compat/deepseek-v3".into(), ModelPricing {
            input_per_1k_usd: 0.00027,
            output_per_1k_usd: 0.0011,
            cache_read_per_1k_usd: 0.000007,
            cache_write_per_1k_usd: 0.00027,
        });
        // ── OpenAI ─────────────────────────────────────────────────────────
        // gpt-4o
        m.insert("openai/gpt-4o".into(), ModelPricing {
            input_per_1k_usd: 0.0025,
            output_per_1k_usd: 0.01,
            cache_read_per_1k_usd: 0.00125,
            cache_write_per_1k_usd: 0.0025,
        });
        // gpt-5
        m.insert("openai/gpt-5".into(), ModelPricing {
            input_per_1k_usd: 0.01,
            output_per_1k_usd: 0.04,
            cache_read_per_1k_usd: 0.005,
            cache_write_per_1k_usd: 0.01,
        });
        m
    }
}

/// 全局懒加载定价表(builtin)。
static BUILTIN_PRICING: once_cell::sync::Lazy<HashMap<String, ModelPricing>> =
    once_cell::sync::Lazy::new(ModelPricing::builtin_table);

// -----------------------------------------------------------------------------
// Catalog 数据结构
// -----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelCatalog {
    #[serde(default = "default_schema_version")]
    pub schema_version: u32,
    pub selected: Selected,
    #[serde(default)]
    pub apis: Vec<ApiEntry>,
}

fn default_schema_version() -> u32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Selected {
    pub api_id: String,
    pub model_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiEntry {
    pub id: String,
    pub display_name: String,
    /// 与 BackendKind 字符串对齐:anthropic / vertex_ai / openai / openai_compat
    pub kind: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub credential_env: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub credential_ref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub base_url: Option<String>,
    #[serde(default)]
    pub models: Vec<ModelEntry>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelEntry {
    pub id: String,
    #[serde(default)]
    pub real_name: Option<String>,
    pub display_name: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub capabilities: Vec<String>,
    /// 计价信息;None 时由 LlmRouter 从 builtin 表回落。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pricing: Option<ModelPricing>,
}

impl ModelCatalog {
    pub async fn load_from_file(path: impl AsRef<Path>) -> Result<Self, LlmError> {
        let text = tokio::fs::read_to_string(path.as_ref()).await?;
        Self::from_json(&text)
    }

    pub fn from_json(text: &str) -> Result<Self, LlmError> {
        let catalog: Self = serde_json::from_str(text)?;
        Ok(catalog)
    }

    pub async fn save_to_file(&self, path: impl AsRef<Path>) -> Result<(), LlmError> {
        let s = serde_json::to_string_pretty(self)?;
        tokio::fs::write(path.as_ref(), s).await.map_err(LlmError::from)
    }

    /// 找出 selected.api_id 指向的 ApiEntry。
    pub fn selected_api(&self) -> Option<&ApiEntry> {
        self.apis.iter().find(|a| a.id == self.selected.api_id)
    }

    /// 找出 selected.api_id + selected.model_id 指向的 ModelEntry。
    pub fn selected_model(&self) -> Option<(&ApiEntry, &ModelEntry)> {
        let api = self.selected_api()?;
        let m = api.models.iter().find(|m| m.id == self.selected.model_id)?;
        Some((api, m))
    }

    /// 把 kind 字段映射到 BackendKind。
    pub fn backend_kind_of(api: &ApiEntry) -> Result<BackendKind, LlmError> {
        match api.kind.as_str() {
            "anthropic" => Ok(BackendKind::Anthropic),
            "vertex_ai" | "vertex" => Ok(BackendKind::Vertex),
            "openai" => Ok(BackendKind::Openai),
            "openai_compat" => Ok(BackendKind::OpenaiCompat),
            other => Err(LlmError::Config(format!("unknown backend kind: {other}"))),
        }
    }
}

impl Default for ModelCatalog {
    fn default() -> Self {
        // 对齐 Python 端 DEFAULT_MODEL_CATALOG 的最小子集。
        Self {
            schema_version: 1,
            selected: Selected {
                api_id: "anthropic".into(),
                model_id: "claude-haiku-4-5".into(),
            },
            apis: vec![
                ApiEntry {
                    id: "vertex_ai".into(),
                    display_name: "Vertex AI".into(),
                    kind: "vertex_ai".into(),
                    enabled: true,
                    credential_ref: Some("rpg/vertex_sa.json".into()),
                    credential_env: None,
                    base_url: None,
                    models: vec![
                        model_entry("gemini-3.5-flash", "Gemini 3.5 Flash"),
                        model_entry("gemini-3.1-pro", "Gemini 3.1 Pro"),
                        model_entry("gemini-2.5-flash", "Gemini 2.5 Flash"),
                    ],
                },
                ApiEntry {
                    id: "anthropic".into(),
                    display_name: "Anthropic".into(),
                    kind: "anthropic".into(),
                    enabled: false,
                    credential_env: Some("ANTHROPIC_API_KEY".into()),
                    credential_ref: None,
                    base_url: None,
                    models: vec![
                        model_entry("claude-opus-4-7", "Claude Opus 4.7"),
                        model_entry("claude-sonnet-4-6", "Claude Sonnet 4.6"),
                        model_entry("claude-haiku-4-5", "Claude Haiku 4.5"),
                    ],
                },
                ApiEntry {
                    id: "openai".into(),
                    display_name: "OpenAI".into(),
                    kind: "openai".into(),
                    enabled: false,
                    credential_env: Some("OPENAI_API_KEY".into()),
                    credential_ref: None,
                    base_url: Some("https://api.openai.com/v1".into()),
                    models: vec![
                        model_entry("gpt-5.5", "GPT-5.5"),
                        model_entry("gpt-5.5-pro", "GPT-5.5 Pro"),
                    ],
                },
            ],
        }
    }
}

fn model_entry(id: &str, display: &str) -> ModelEntry {
    ModelEntry {
        id: id.into(),
        real_name: Some(id.into()),
        display_name: display.into(),
        enabled: true,
        capabilities: vec!["text".into(), "streaming".into(), "tools".into()],
        pricing: None,
    }
}

// -----------------------------------------------------------------------------
// Router
// -----------------------------------------------------------------------------

#[derive(Clone, Default)]
pub struct LlmRouter {
    /// 按 (BackendKind, api_id) 索引,因为同一个 kind 可能对应多个 provider
    /// (e.g. openai_compat 下 deepseek / moonshot / kimi 各一份 backend)。
    ///
    /// 6B-3:`Arc<dyn LlmBackend>` → `Arc<AnyBackend>` enum 静态分派,去虚表。
    backends: HashMap<RouterKey, Arc<AnyBackend>>,
    catalog: Option<ModelCatalog>,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct RouterKey {
    kind: BackendKind,
    api_id: String,
}

impl LlmRouter {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_catalog(mut self, catalog: ModelCatalog) -> Self {
        self.catalog = Some(catalog);
        self
    }

    pub fn catalog(&self) -> Option<&ModelCatalog> {
        self.catalog.as_ref()
    }

    pub fn set_catalog(&mut self, catalog: ModelCatalog) {
        self.catalog = Some(catalog);
    }

    /// 注册 backend。`api_id` 与 catalog 中 apis[].id 对齐;同一个 backend 实例
    /// 可以注册多次 (同 kind / 不同 api_id) 实现透传。
    ///
    /// 6B-3:接收 `Arc<AnyBackend>`(enum 静态分派)。caller 用
    /// `Arc::new(AnyBackend::from(concrete_backend))` 或 `.into()` 构造。
    pub fn register(&mut self, api_id: impl Into<String>, backend: Arc<AnyBackend>) {
        let kind = backend.kind();
        self.backends.insert(
            RouterKey {
                kind,
                api_id: api_id.into(),
            },
            backend,
        );
    }

    /// 取 selected.api_id 当前选中的 backend。
    pub fn current_backend(&self) -> Result<Arc<AnyBackend>, LlmError> {
        let catalog = self
            .catalog
            .as_ref()
            .ok_or_else(|| LlmError::Config("router: no catalog set".into()))?;
        let api = catalog
            .selected_api()
            .ok_or_else(|| LlmError::Config("router: selected api_id not found in catalog".into()))?;
        let kind = ModelCatalog::backend_kind_of(api)?;
        self.backends
            .get(&RouterKey {
                kind,
                api_id: api.id.clone(),
            })
            .cloned()
            .ok_or_else(|| {
                LlmError::Config(format!(
                    "router: backend not registered for {} ({})",
                    api.id, kind
                ))
            })
    }

    /// 显式按 api_id 选择(不改变 selected)。
    pub fn backend_for_api(&self, api_id: &str) -> Result<Arc<AnyBackend>, LlmError> {
        let catalog = self
            .catalog
            .as_ref()
            .ok_or_else(|| LlmError::Config("router: no catalog set".into()))?;
        let api = catalog
            .apis
            .iter()
            .find(|a| a.id == api_id)
            .ok_or_else(|| LlmError::Config(format!("router: no api {api_id} in catalog")))?;
        let kind = ModelCatalog::backend_kind_of(api)?;
        self.backends
            .get(&RouterKey {
                kind,
                api_id: api.id.clone(),
            })
            .cloned()
            .ok_or_else(|| {
                LlmError::Config(format!("router: backend not registered for {api_id}"))
            })
    }

    /// 转发 stream_chat;`req.model` 若为空则用 selected.model_id。
    /// 返回流的生命周期与 router 借用绑定。
    pub async fn stream_chat<'a>(
        &'a self,
        mut req: ChatRequest,
    ) -> Result<ChunkStream<'a>, LlmError> {
        if req.model.is_empty() {
            if let Some(catalog) = &self.catalog {
                req.model = catalog.selected.model_id.clone();
            }
        }
        // 直接借出 router 内部持有的 Arc:返回的 ChunkStream<'a> 借的是
        // backends map 里那个 Arc 的内部 dyn LlmBackend,生命周期 ≤ 'a。
        let key = {
            let catalog = self
                .catalog
                .as_ref()
                .ok_or_else(|| LlmError::Config("router: no catalog set".into()))?;
            let api = catalog.selected_api().ok_or_else(|| {
                LlmError::Config("router: selected api_id not found in catalog".into())
            })?;
            let kind = ModelCatalog::backend_kind_of(api)?;
            RouterKey {
                kind,
                api_id: api.id.clone(),
            }
        };
        let backend: &Arc<AnyBackend> = self.backends.get(&key).ok_or_else(|| {
            LlmError::Config(format!(
                "router: backend not registered for {} ({})",
                key.api_id, key.kind
            ))
        })?;
        backend.as_ref().stream_chat(req).await
    }

    /// 罗列所有已注册 backend 上的模型,合并去重。
    pub async fn list_all_models(&self) -> Vec<ModelInfo> {
        let mut out: Vec<ModelInfo> = Vec::new();
        for backend in self.backends.values() {
            if let Ok(models) = backend.list_models().await {
                out.extend(models);
            }
        }
        out.sort_by(|a, b| a.id.cmp(&b.id));
        out.dedup_by(|a, b| a.id == b.id);
        out
    }

    /// 从数据库动态加载 catalog。
    ///
    /// 表结构(对应 migrations/001_init.sql + 003_ensure_model_apis_base_url.sql):
    /// ```sql
    /// CREATE TABLE model_apis (
    ///   api_id       text PRIMARY KEY,       -- NOTE: PK 列名是 api_id,不是 id
    ///   display_name text NOT NULL DEFAULT '',
    ///   kind         text NOT NULL,          -- anthropic/vertex_ai/openai/openai_compat
    ///   enabled      boolean NOT NULL DEFAULT true,
    ///   credential_env  text NOT NULL DEFAULT '',
    ///   credential_ref  text NOT NULL DEFAULT '',
    ///   base_url     text NOT NULL DEFAULT '' -- added by v003
    /// );
    /// CREATE TABLE model_entries (           -- 曾叫 model_models
    ///   id           bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    ///   api_id       text NOT NULL REFERENCES model_apis(api_id) ON DELETE CASCADE,
    ///   model_id     text NOT NULL,
    ///   real_name    text NOT NULL,
    ///   display_name text NOT NULL DEFAULT '',
    ///   enabled      boolean NOT NULL DEFAULT true,
    ///   capabilities jsonb NOT NULL DEFAULT '[]',
    ///   UNIQUE(api_id, model_id)
    /// );
    /// ```
    /// 注:pricing 列不在 DB schema;定价由运行时 config 或 metadata jsonb 提供。
    #[cfg(feature = "db")]
    pub async fn load_from_db(pool: &sqlx::PgPool) -> Result<Self, LlmError> {
        // 1. 加载 model_apis
        // schema: api_id(PK), display_name, kind, enabled, credential_ref, credential_env,
        //         metadata, created_at, updated_at; base_url added by v003 migration.
        let api_rows = sqlx::query(
            "SELECT api_id, display_name, kind, enabled, credential_env, credential_ref, base_url \
             FROM model_apis ORDER BY api_id"
        )
        .fetch_all(pool)
        .await
        .map_err(|e| LlmError::Config(format!("load_from_db model_apis: {e}")))?;

        let mut apis: Vec<ApiEntry> = Vec::with_capacity(api_rows.len());
        for row in &api_rows {
            use sqlx::Row;
            apis.push(ApiEntry {
                id: row.try_get::<String, _>("api_id")
                    .map_err(|e| LlmError::Config(e.to_string()))?,
                display_name: row.try_get::<String, _>("display_name")
                    .unwrap_or_default(),
                kind: row.try_get::<String, _>("kind")
                    .map_err(|e| LlmError::Config(e.to_string()))?,
                enabled: row.try_get::<bool, _>("enabled").unwrap_or(true),
                credential_env: row.try_get::<Option<String>, _>("credential_env").ok().flatten(),
                credential_ref: row.try_get::<Option<String>, _>("credential_ref").ok().flatten(),
                base_url: row.try_get::<Option<String>, _>("base_url").ok().flatten(),
                models: vec![],
            });
        }

        // 2. 加载 model_entries (曾叫 model_models)
        // schema: id(bigint), api_id, model_id, real_name, display_name, enabled,
        //         capabilities(jsonb), metadata, created_at, updated_at.
        // 价格列不在 DB schema 里,定价用 metadata jsonb 或运行时配置替代。
        let model_rows = sqlx::query(
            "SELECT api_id, model_id, real_name, display_name, enabled, capabilities \
             FROM model_entries ORDER BY api_id, model_id"
        )
        .fetch_all(pool)
        .await
        .map_err(|e| LlmError::Config(format!("load_from_db model_entries: {e}")))?;

        for row in &model_rows {
            use sqlx::Row;
            let api_id: String = row.try_get("api_id")
                .map_err(|e| LlmError::Config(e.to_string()))?;
            let model_id: String = row.try_get("model_id")
                .map_err(|e| LlmError::Config(e.to_string()))?;

            // 价格列在 model_entries 中不存在;pricing 设为 None。
            let pricing: Option<ModelPricing> = None;

            let caps: Vec<String> = {
                // capabilities 是 jsonb array of string
                let v: serde_json::Value = row.try_get::<serde_json::Value, _>("capabilities")
                    .unwrap_or(serde_json::Value::Array(vec![]));
                v.as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|x| x.as_str().map(|s| s.to_string()))
                            .collect()
                    })
                    .unwrap_or_default()
            };

            let entry = ModelEntry {
                id: model_id,
                real_name: row.try_get::<Option<String>, _>("real_name").ok().flatten(),
                display_name: row.try_get::<String, _>("display_name").unwrap_or_default(),
                enabled: row.try_get::<bool, _>("enabled").unwrap_or(true),
                capabilities: caps,
                pricing,
            };

            if let Some(api) = apis.iter_mut().find(|a| a.id == api_id) {
                api.models.push(entry);
            }
        }

        // 3. 从 app_config 读取持久化的 selected_model(与 Python _load_model_catalog_from_db 对齐)
        let persisted_selected: Option<Selected> = {
            let row: Option<(serde_json::Value,)> = sqlx::query_as(
                "SELECT value FROM app_config WHERE key = 'selected_model'",
            )
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

            row.and_then(|(v,)| serde_json::from_value::<Selected>(v).ok())
        };

        // 4. 构造 catalog:若 DB 里没有任何 api,回落到默认
        let catalog = if apis.is_empty() {
            ModelCatalog::default()
        } else {
            // 优先使用持久化的 selected_model;若无则回落到第一个 enabled api+model
            let selected = persisted_selected.unwrap_or_else(|| {
                apis.iter()
                    .find(|a| a.enabled)
                    .and_then(|a| a.models.iter().find(|m| m.enabled).map(|m| (a, m)))
                    .map(|(a, m)| Selected {
                        api_id: a.id.clone(),
                        model_id: m.id.clone(),
                    })
                    .unwrap_or_else(|| Selected {
                        api_id: apis[0].id.clone(),
                        model_id: apis[0].models.first().map(|m| m.id.clone()).unwrap_or_default(),
                    })
            });
            ModelCatalog {
                schema_version: 1,
                selected,
                apis,
            }
        };

        Ok(Self {
            backends: HashMap::new(),
            catalog: Some(catalog),
        })
    }

    /// 查询 (api_id, model_id) 的定价;先找 catalog 里的 ModelEntry.pricing,
    /// 没有则回落到 builtin 表。
    pub fn pricing_for(&self, api_id: &str, model_id: &str) -> Option<&ModelPricing> {
        // 1. catalog 内联 pricing
        if let Some(catalog) = &self.catalog {
            if let Some(api) = catalog.apis.iter().find(|a| a.id == api_id) {
                if let Some(model) = api.models.iter().find(|m| m.id == model_id) {
                    if model.pricing.is_some() {
                        return model.pricing.as_ref();
                    }
                }
            }
        }
        // 2. 回落 builtin
        let key = format!("{api_id}/{model_id}");
        BUILTIN_PRICING.get(&key)
    }
}

// -----------------------------------------------------------------------------
// KeepAliveStream — 把 Arc<dyn LlmBackend> 与 inner Stream 捆绑,
// 让返回的 stream 拥有 'static lifetime。
// -----------------------------------------------------------------------------

use std::pin::Pin;
use std::task::{Context, Poll};
use futures_util::Stream;
use crate::pipeline::ChatChunk;

#[allow(dead_code)]
struct KeepAliveStream {
    /// 持有 backend Arc，确保在流被 poll 期间不被 drop
    _keep: Arc<dyn LlmBackend>,
    inner: Pin<Box<dyn futures_util::Stream<Item = Result<ChatChunk, LlmError>> + Send + 'static>>,
}

impl Stream for KeepAliveStream {
    type Item = Result<ChatChunk, LlmError>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        // SAFETY: we do not move out of `self`, only forward the inner pin
        let this = unsafe { self.get_unchecked_mut() };
        this.inner.as_mut().poll_next(cx)
    }
}

// -----------------------------------------------------------------------------
// Probe (轻量,对齐 model_probe.py)
// -----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProbeResult {
    pub api_id: String,
    pub model_id: String,
    pub ok: bool,
    pub error: Option<String>,
}

/// 跑一个最小 chat 请求看 backend / model 是否真的可用。
/// 与 Python `model_probe.py` 不同的是,这里只测试 stream_chat 能不能 200,
/// 不解析具体输出 — 留给上层做。
pub async fn probe_backend(
    backend: &dyn LlmBackend,
    api_id: &str,
    model_id: &str,
) -> ProbeResult {
    let req = ChatRequest {
        model: model_id.to_string(),
        system: Some("ping".into()),
        messages: vec![crate::pipeline::ChatMessage::user("ping")],
        max_tokens: Some(8),
        stream: false,
        ..Default::default()
    };
    match backend.stream_chat(req).await {
        Ok(mut s) => {
            use futures_util::StreamExt;
            let mut ok = false;
            while let Some(chunk) = s.next().await {
                if chunk.is_ok() {
                    ok = true;
                    break;
                }
            }
            ProbeResult {
                api_id: api_id.into(),
                model_id: model_id.into(),
                ok,
                error: if ok {
                    None
                } else {
                    Some("no chunks received".into())
                },
            }
        }
        Err(e) => ProbeResult {
            api_id: api_id.into(),
            model_id: model_id.into(),
            ok: false,
            error: Some(e.to_string()),
        },
    }
}

// ---- 按 provider 走专属轻量探测路径(与 Python 端 model_probe.py 对齐)。----

/// Anthropic 专用探测:`POST /v1/messages` 用 `max_tokens=1` 做最便宜调用。
pub async fn probe_anthropic(
    backend: &crate::anthropic::AnthropicBackend,
    api_id: &str,
    model_id: &str,
) -> ProbeResult {
    use futures_util::StreamExt;
    let req = ChatRequest {
        model: model_id.to_string(),
        messages: vec![crate::pipeline::ChatMessage::user(".")],
        max_tokens: Some(1),
        stream: false,
        ..Default::default()
    };
    match backend.stream_chat(req).await {
        Ok(mut s) => {
            let mut got = false;
            while let Some(c) = s.next().await {
                if c.is_ok() {
                    got = true;
                    break;
                }
            }
            ProbeResult {
                api_id: api_id.into(),
                model_id: model_id.into(),
                ok: got,
                error: if got {
                    None
                } else {
                    Some("no chunks".into())
                },
            }
        }
        Err(e) => ProbeResult {
            api_id: api_id.into(),
            model_id: model_id.into(),
            ok: false,
            error: Some(e.to_string()),
        },
    }
}

/// Vertex 专用探测:`GET .../publishers/google/models/{model}` (Publisher Model resource),
/// 不计费、不消耗 quota。
pub async fn probe_vertex(
    backend: &crate::vertex::VertexBackend,
    api_id: &str,
    model_id: &str,
) -> ProbeResult {
    match backend.head_publisher_model(model_id).await {
        Ok(true) => ProbeResult {
            api_id: api_id.into(),
            model_id: model_id.into(),
            ok: true,
            error: None,
        },
        Ok(false) => ProbeResult {
            api_id: api_id.into(),
            model_id: model_id.into(),
            ok: false,
            error: Some("publisher model not found".into()),
        },
        Err(e) => ProbeResult {
            api_id: api_id.into(),
            model_id: model_id.into(),
            ok: false,
            error: Some(e.to_string()),
        },
    }
}

/// OpenAI / openai_compat 专用探测:先 `GET /v1/models`,失败回退到最小 chat。
pub async fn probe_openai(
    backend: &crate::openai::OpenAiBackend,
    api_id: &str,
    model_id: &str,
) -> ProbeResult {
    use futures_util::StreamExt;
    // 1. /v1/models
    if let Ok(models) = backend.list_models().await {
        if models.iter().any(|m| m.id == model_id) {
            return ProbeResult {
                api_id: api_id.into(),
                model_id: model_id.into(),
                ok: true,
                error: None,
            };
        }
    }
    // 2. 最小 chat。
    let req = ChatRequest {
        model: model_id.to_string(),
        messages: vec![crate::pipeline::ChatMessage::user(".")],
        max_tokens: Some(1),
        stream: false,
        ..Default::default()
    };
    match backend.stream_chat(req).await {
        Ok(mut s) => {
            let mut got = false;
            while let Some(c) = s.next().await {
                if c.is_ok() {
                    got = true;
                    break;
                }
            }
            ProbeResult {
                api_id: api_id.into(),
                model_id: model_id.into(),
                ok: got,
                error: if got {
                    None
                } else {
                    Some("no chunks".into())
                },
            }
        }
        Err(e) => ProbeResult {
            api_id: api_id.into(),
            model_id: model_id.into(),
            ok: false,
            error: Some(e.to_string()),
        },
    }
}
