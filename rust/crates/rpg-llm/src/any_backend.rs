//! any_backend — enum 静态分派,消除 `Arc<dyn LlmBackend>` 的虚表 + per-call Box。
//!
//! backend 种类固定且少(Anthropic / Vertex / OpenAi / Responses),用 enum 把
//! 动态分派折成静态 `match`。`LlmRouter` 内部存 `Arc<AnyBackend>` 替代
//! `Arc<dyn LlmBackend>`;`rpg-agents` 的 `SharedLlm` 同样改成 `Arc<AnyBackend>`。
//!
//! 各 backend 仍各自 `impl LlmBackend`(改动最小,enum 内部转调具体方法),
//! 且 `AnyBackend` 也 `impl LlmBackend`,这样 agents 里既有的
//! `&dyn LlmBackend` adapter helper(common.rs 的 call_text / call_with_tools 等)
//! 无需改写,只是改为持有具体 enum。

use crate::anthropic::AnthropicBackend;
use crate::openai::OpenAiBackend;
use crate::pipeline::{
    BackendKind, ChatRequest, ChunkStream, LlmBackend, LlmError, ModelInfo,
};
use crate::responses::ResponsesBackend;
use crate::vertex::VertexBackend;

/// 所有 LLM provider 的静态分派枚举。
///
/// 相比 `Arc<dyn LlmBackend>`:
///   * `stream_chat` / `list_models` / `embed` 走 `match` 直接调具体方法,
///     无虚表查找;
///   * `#[async_trait]` 的 `Box<dyn Future>` 每调用堆分配只发生在最内层具体
///     backend(本就无法避免,因为返回的是 `ChunkStream = Pin<Box<dyn Stream>>`),
///     enum 这层不再额外套一层 trait-object dispatch。
///
/// 始终以 `Arc<AnyBackend>` 共享持有,故不需要 `Clone`(避免强制所有内部
/// backend 都 derive Clone)。
pub enum AnyBackend {
    Anthropic(AnthropicBackend),
    Vertex(VertexBackend),
    OpenAi(OpenAiBackend),
    Responses(ResponsesBackend),
}

impl AnyBackend {
    /// backend 分类。委托给内部具体 backend(注意:`OpenAiBackend` 会按
    /// base_url 动态返回 Openai / OpenaiCompat,不能在此硬编码)。
    pub fn kind(&self) -> BackendKind {
        match self {
            Self::Anthropic(b) => b.kind(),
            Self::Vertex(b) => b.kind(),
            Self::OpenAi(b) => b.kind(),
            Self::Responses(b) => b.kind(),
        }
    }

    /// 主路径:流式 ChatChunk。`'a` 借的是具体 backend `&self`,与 trait 一致。
    pub async fn stream_chat<'a>(
        &'a self,
        req: ChatRequest,
    ) -> Result<ChunkStream<'a>, LlmError> {
        match self {
            Self::Anthropic(b) => b.stream_chat(req).await,
            Self::Vertex(b) => b.stream_chat(req).await,
            Self::OpenAi(b) => b.stream_chat(req).await,
            Self::Responses(b) => b.stream_chat(req).await,
        }
    }

    /// 罗列模型。
    pub async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
        match self {
            Self::Anthropic(b) => b.list_models().await,
            Self::Vertex(b) => b.list_models().await,
            Self::OpenAi(b) => b.list_models().await,
            Self::Responses(b) => b.list_models().await,
        }
    }

    /// 文本 embedding(Vertex / OpenAI 支持,其余回落 Unsupported)。
    pub async fn embed(&self, model: &str, texts: &[String]) -> Result<Vec<Vec<f32>>, LlmError> {
        match self {
            Self::Anthropic(b) => b.embed(model, texts).await,
            Self::Vertex(b) => b.embed(model, texts).await,
            Self::OpenAi(b) => b.embed(model, texts).await,
            Self::Responses(b) => b.embed(model, texts).await,
        }
    }
}

// 便捷 From 转换,方便 caller 直接 `AnyBackend::from(backend)` / `.into()`。
impl From<AnthropicBackend> for AnyBackend {
    fn from(b: AnthropicBackend) -> Self {
        Self::Anthropic(b)
    }
}
impl From<VertexBackend> for AnyBackend {
    fn from(b: VertexBackend) -> Self {
        Self::Vertex(b)
    }
}
impl From<OpenAiBackend> for AnyBackend {
    fn from(b: OpenAiBackend) -> Self {
        Self::OpenAi(b)
    }
}
impl From<ResponsesBackend> for AnyBackend {
    fn from(b: ResponsesBackend) -> Self {
        Self::Responses(b)
    }
}

// 让 AnyBackend 同时满足 LlmBackend trait:agents 里既有的 `&dyn LlmBackend`
// adapter helper(call_text / call_structured / stream_text / call_with_tools /
// supports_native_tools)无需改写。trait 方法直接转调上面的 inherent 方法。
#[async_trait::async_trait]
impl LlmBackend for AnyBackend {
    fn kind(&self) -> BackendKind {
        AnyBackend::kind(self)
    }

    async fn stream_chat<'a>(&'a self, req: ChatRequest) -> Result<ChunkStream<'a>, LlmError> {
        AnyBackend::stream_chat(self, req).await
    }

    async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
        AnyBackend::list_models(self).await
    }

    async fn embed(&self, model: &str, texts: &[String]) -> Result<Vec<Vec<f32>>, LlmError> {
        AnyBackend::embed(self, model, texts).await
    }
}
