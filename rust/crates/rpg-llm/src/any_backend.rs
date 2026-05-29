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

use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Instant;

use futures_util::stream::Stream;

use crate::anthropic::AnthropicBackend;
use crate::metrics;
use crate::openai::OpenAiBackend;
use crate::pipeline::{
    BackendKind, ChatChunk, ChatRequest, ChunkStream, LlmBackend, LlmError, ModelInfo, Usage,
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
    ///
    /// 埋点:
    ///   * `llm_request_duration_seconds` — 从发请求到内层 stream 首帧就绪的延迟;
    ///     流本身用 [`InstrumentedStream`] 包裹,在 stream 关闭时发 `ok`/`error` 计数。
    ///   * `llm_request_total` — 请求结果计数(ok / error)。
    ///   * `llm_tokens_used_total` — 从 `ChatChunk::Usage` 取 token 数。
    pub async fn stream_chat<'a>(
        &'a self,
        req: ChatRequest,
    ) -> Result<ChunkStream<'a>, LlmError> {
        let backend_label = self.kind().to_string();
        let model_label = req.model.clone();
        let start = Instant::now();

        let inner_result = match self {
            Self::Anthropic(b) => b.stream_chat(req).await,
            Self::Vertex(b) => b.stream_chat(req).await,
            Self::OpenAi(b) => b.stream_chat(req).await,
            Self::Responses(b) => b.stream_chat(req).await,
        };

        match inner_result {
            Err(e) => {
                metrics::record_llm_request(&backend_label, start.elapsed(), false);
                Err(e)
            }
            Ok(inner) => {
                // 首帧延迟(发请求到首次可 poll)在此记录;流消费延迟由 caller 自行测量。
                metrics::record_llm_request(&backend_label, start.elapsed(), true);
                let stream = InstrumentedStream {
                    inner,
                    backend: backend_label,
                    model: model_label,
                    usage: Usage::default(),
                };
                Ok(Box::pin(stream))
            }
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

// ── InstrumentedStream ───────────────────────────────────────────────────────

/// 包裹内层 `ChunkStream`,拦截 `ChatChunk::Usage` 并在流结束时发 token 计数。
///
/// Drop 时如果累积到 `Usage` 数据则记录一次 `llm_tokens_used_total`。
struct InstrumentedStream<'a> {
    inner: ChunkStream<'a>,
    backend: String,
    model: String,
    usage: Usage,
}

impl<'a> Stream for InstrumentedStream<'a> {
    type Item = Result<ChatChunk, LlmError>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        // SAFETY: Pin 投影到内层 stream。inner 不含自引用,投影安全。
        let inner = unsafe { self.as_mut().map_unchecked_mut(|s| &mut s.inner) };
        match inner.poll_next(cx) {
            Poll::Ready(Some(Ok(ChatChunk::Usage(u)))) => {
                // 累加(部分 provider 可能分多帧发 usage)。
                let s = self.get_mut();
                s.usage.input_tokens = s.usage.input_tokens.saturating_add(u.input_tokens);
                s.usage.output_tokens = s.usage.output_tokens.saturating_add(u.output_tokens);
                s.usage.cache_read = s.usage.cache_read.saturating_add(u.cache_read);
                // 透传 Usage chunk 给 caller。
                Poll::Ready(Some(Ok(ChatChunk::Usage(u))))
            }
            Poll::Ready(None) => {
                // 流结束:刷 token 指标。
                let s = self.get_mut();
                metrics::record_llm_tokens(
                    &s.backend,
                    &s.model,
                    s.usage.input_tokens,
                    s.usage.output_tokens,
                    s.usage.cache_read,
                );
                // 重置防 Drop 重复记录。
                s.usage = Usage::default();
                Poll::Ready(None)
            }
            other => other,
        }
    }
}

impl<'a> Drop for InstrumentedStream<'a> {
    fn drop(&mut self) {
        // 如果 caller 提前 drop stream(中断),也把已累积的 token 数发出。
        if self.usage.input_tokens > 0 || self.usage.output_tokens > 0 {
            metrics::record_llm_tokens(
                &self.backend,
                &self.model,
                self.usage.input_tokens,
                self.usage.output_tokens,
                self.usage.cache_read,
            );
        }
    }
}
