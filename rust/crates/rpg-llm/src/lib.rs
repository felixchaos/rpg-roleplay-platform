//! rpg-llm — Anthropic / Vertex / OpenAI 客户端。
//!
//! 对应 Python:
//!   - rpg/agents/gm/backends/{anthropic,vertex,openai_compat}.py
//!   - rpg/chat_pipeline.py (SSEEvent 抽象)
//!   - rpg/model_registry.py / rpg/model_probe.py
//!
//! 设计要点:
//!   * `pipeline.rs` 给所有 backend 一个统一的 `ChatRequest`/`ChatChunk`/`LlmBackend`
//!     trait;`ChatChunk::ToolCall` 总是合并完才发(状态机由 backend 内部维护)。
//!   * `anthropic.rs` 用 reqwest + eventsource-stream 自实现 SSE,完整覆盖
//!     message_start / content_block_* / message_delta / message_stop。
//!     tool_use 增量 JSON 在 `ToolCallAccumulator` 里累加 partial_json,
//!     content_block_stop 时一次性 emit ToolCall。
//!   * `vertex.rs` 用 yup-oauth2 ServiceAccount 取 token,SSE 端点 `?alt=sse`,
//!     支持 generateContent / streamGenerateContent / embedContent(:predict)。
//!   * `openai.rs` 自实现 chat/completions + stream;基于 base_url override 同时
//!     兼任 DeepSeek / Kimi / Moonshot / OpenRouter 等 provider。
//!   * `registry.rs` 提供 catalog 加载 (与 Python model_catalog.json 兼容)
//!     和 LlmRouter (按 selected.api_id → BackendKind 分发)。
//!
//! 已覆盖:
//!   * Anthropic extended thinking (thinking_delta / signature_delta) 已映射成
//!     `ChatChunk::Thinking`,thinking 配置通过 `req.extra.thinking` 或
//!     `req.extra.thinking_budget` 透传。
//!   * Vertex 多模态:`MessagePart::FileData` 走 fileData (gs:// / https://),
//!     `MessagePart::InlineData` 走 inlineData (base64,支持 audio/wav 等)。
//!   * OpenAI Responses API:`responses.rs` 模块新增 `ResponsesBackend`,
//!     处理 `/v1/responses` 端点和对应 SSE 流。
//!   * `model_probe` 拆出三个 provider 专属探测函数 (`probe_anthropic` /
//!     `probe_vertex` / `probe_openai`),Vertex 走 publisher model GET 不计费。
//!
//! 未覆盖的 TODO:
//!   * OpenAI Realtime (WebSocket / WebRTC) - 暂不实现,后续若需要再补 ws 通路。

pub mod anthropic;
pub mod any_backend;
pub mod metrics;
pub mod openai;
pub mod pipeline;
pub mod registry;
pub mod responses;
pub mod simd_parse;
pub mod vertex;

/// 服务端 max_tokens 硬上限。任何 backend 在 build body 时都把客户端传入的
/// `max_tokens` 通过 `.min(HARD_MAX_OUTPUT_TOKENS)` clamp,忽略超限值,
/// 防止盗 session / 恶意请求把单次 output 拉到天价。
///
/// 注:与 `rpg-platform::quota::HARD_MAX_TOKENS` 数值一致(两 crate 无依赖环,
/// 故各自持常量)。改动需同步两处。
pub const HARD_MAX_OUTPUT_TOKENS: u32 = 8192;

// Prelude:常用类型 reexport,方便上层 (rpg-agents) 引用。
pub use pipeline::{
    build_thinking_extra, merge_thinking_extra, BackendKind, ChatChunk, ChatMessage, ChatRequest,
    ChatRole, ChunkStream, LlmBackend, LlmError, MessagePart, ModelInfo, ToolCall, ToolSchema,
    Usage,
};

pub use anthropic::AnthropicBackend;
pub use any_backend::AnyBackend;
pub use openai::OpenAiBackend;
pub use registry::{
    probe_anthropic, probe_backend, probe_openai, probe_vertex, ApiEntry, LlmRouter, ModelCatalog,
    ModelEntry, ModelPricing, ProbeResult, Selected,
};
pub use responses::ResponsesBackend;
pub use vertex::VertexBackend;
