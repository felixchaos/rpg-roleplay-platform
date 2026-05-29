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

use crate::pipeline::{BackendKind, ChunkStream, LlmBackend, LlmError, ModelInfo};
use crate::pipeline::{ChatRequest};

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
}

impl ModelCatalog {
    pub fn load_from_file(path: impl AsRef<Path>) -> Result<Self, LlmError> {
        let text = std::fs::read_to_string(path.as_ref())?;
        Self::from_json(&text)
    }

    pub fn from_json(text: &str) -> Result<Self, LlmError> {
        let catalog: Self = serde_json::from_str(text)?;
        Ok(catalog)
    }

    pub fn save_to_file(&self, path: impl AsRef<Path>) -> Result<(), LlmError> {
        let s = serde_json::to_string_pretty(self)?;
        std::fs::write(path.as_ref(), s).map_err(LlmError::from)
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
                api_id: "vertex_ai".into(),
                model_id: "gemini-3.5-flash".into(),
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
    }
}

// -----------------------------------------------------------------------------
// Router
// -----------------------------------------------------------------------------

#[derive(Clone, Default)]
pub struct LlmRouter {
    /// 按 (BackendKind, api_id) 索引,因为同一个 kind 可能对应多个 provider
    /// (e.g. openai_compat 下 deepseek / moonshot / kimi 各一份 backend)。
    backends: HashMap<RouterKey, Arc<dyn LlmBackend>>,
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
    pub fn register(&mut self, api_id: impl Into<String>, backend: Arc<dyn LlmBackend>) {
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
    pub fn current_backend(&self) -> Result<Arc<dyn LlmBackend>, LlmError> {
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
    pub fn backend_for_api(&self, api_id: &str) -> Result<Arc<dyn LlmBackend>, LlmError> {
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
        let backend: &Arc<dyn LlmBackend> = self.backends.get(&key).ok_or_else(|| {
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
