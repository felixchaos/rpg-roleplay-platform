//! pipeline — 统一的 LLM 抽象:ChatRequest / ChatChunk / LlmBackend trait。
//!
//! 对应 Python 侧 `rpg/chat_pipeline.py` 中的 SSEEvent + 各 backend 的
//! `stream` / `stream_with_mcp_loop` yields。这里把所有 provider 的 yield 值
//! 折叠成一组统一枚举:`ChatChunk`,由 GameMaster 层做语义解释。
//!
//! 本模块不做 MCP loop,只暴露 raw 流;MCP 协同在 rpg-agents 里实现。

use std::collections::HashMap;
use std::fmt;
use std::pin::Pin;

use async_trait::async_trait;
use futures_util::stream::Stream;
use serde::{Deserialize, Serialize};

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

// -----------------------------------------------------------------------------
// 角色 / 消息
// -----------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash, Default)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
#[serde(rename_all = "lowercase")]
pub enum ChatRole {
    #[default]
    User,
    Assistant,
    System,
    Tool,
}

impl fmt::Display for ChatRole {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ChatRole::User => f.write_str("user"),
            ChatRole::Assistant => f.write_str("assistant"),
            ChatRole::System => f.write_str("system"),
            ChatRole::Tool => f.write_str("tool"),
        }
    }
}

/// 同一条消息可以是纯文本,也可以是 multipart (image / tool_result / tool_use)。
/// `extra` 用来透传 provider 特定字段 (e.g. cache_control)。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct ChatMessage {
    pub role: ChatRole,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub parts: Vec<MessagePart>,
    /// 当 role=tool 时使用 (OpenAI 兼容)。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// 当 role=assistant 且包含 tool_use 时,这里是 assistant 调用的 tool。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tool_calls: Vec<ToolCall>,
    /// provider 特定附加字段 (Anthropic cache_control 等)。
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub extra: serde_json::Value,
}

impl ChatMessage {
    pub fn user(text: impl Into<String>) -> Self {
        Self {
            role: ChatRole::User,
            content: Some(text.into()),
            ..Default::default()
        }
    }
    pub fn assistant(text: impl Into<String>) -> Self {
        Self {
            role: ChatRole::Assistant,
            content: Some(text.into()),
            ..Default::default()
        }
    }
    pub fn system(text: impl Into<String>) -> Self {
        Self {
            role: ChatRole::System,
            content: Some(text.into()),
            ..Default::default()
        }
    }
    pub fn tool_result(call_id: impl Into<String>, payload: impl Into<String>) -> Self {
        Self {
            role: ChatRole::Tool,
            tool_call_id: Some(call_id.into()),
            content: Some(payload.into()),
            ..Default::default()
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum MessagePart {
    Text {
        text: String,
    },
    Image {
        /// base64 数据 URL 或远端 URL。
        source: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        media_type: Option<String>,
    },
    /// Vertex / Gemini 风格 file_data:引用 GCS 或 https 上传后的 URI。
    /// 也用于 Anthropic 的 file blocks (Vision Beta) 透传。
    FileData {
        mime_type: String,
        file_uri: String,
    },
    /// Vertex / Gemini 风格 inline_data:直接嵌入 base64 bytes
    /// (音频 audio/wav / audio/mp3、视频、PDF、原始图像皆可)。
    InlineData {
        mime_type: String,
        /// 已解码的原始字节;backend 内部按需 base64 编码。
        data: Vec<u8>,
    },
    ToolUse {
        id: String,
        name: String,
        input: serde_json::Value,
    },
    ToolResult {
        tool_use_id: String,
        content: String,
        #[serde(default)]
        is_error: bool,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    /// 已合并完的 JSON object;流式累加由 backend 在拼完后再 emit。
    pub input: serde_json::Value,
}

// -----------------------------------------------------------------------------
// 工具 schema (统一表示)
// -----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct ToolSchema {
    pub name: String,
    #[serde(default)]
    pub description: String,
    /// JSONSchema (OpenAPI 风格) 描述 input。
    pub input_schema: serde_json::Value,
    /// 可选的 server_id (来自 MCP);backend 用作 namespace 前缀。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub server_id: Option<String>,
}

// -----------------------------------------------------------------------------
// 请求
// -----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct ChatRequest {
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub system: Option<String>,
    pub messages: Vec<ChatMessage>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tools: Vec<ToolSchema>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<u32>,
    /// true 时 stream_chat 返回逐 token 的 chunk;false 时一次性返回。
    #[serde(default = "default_true")]
    pub stream: bool,
    /// provider 特定补丁 (anthropic cache_control / vertex thinking_budget / openai response_format 等)
    #[serde(default)]
    pub extra: serde_json::Value,
}

fn default_true() -> bool {
    true
}

// -----------------------------------------------------------------------------
// 响应 chunk
// -----------------------------------------------------------------------------

/// 不直接导出 ts-rs 类型 — `ChatChunk` 是 backend 间的内部流通格式,含 tuple
/// variant (`Text(String)` 等),ts-rs 12 对 `#[serde(tag)]` + newtype variant
/// 会生成形如 `{type:"text"} & string` 的非法 TS(等价 never)。前端 SSE wire
/// chunk 走 [`crate::pipeline::WireChatChunk`] 命名字段版本。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChatChunk {
    /// 流式 token / 文本片段。
    Text(String),
    /// Extended thinking / reasoning 文本片段(Anthropic thinking_delta /
    /// Gemini thought / OpenAI reasoning summary 的统一抽象)。
    Thinking(String),
    /// 工具调用 (input 已合并完成)。
    ToolCall {
        id: String,
        name: String,
        input: serde_json::Value,
    },
    /// 用量统计 (流末尾或独立 chunk)。
    Usage(Usage),
    /// 模型自然结束。
    Stop { reason: String },
    /// 非致命错误 (例如 partial JSON 解析失败)。
    Error(String),
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct Usage {
    #[serde(default)]
    pub input_tokens: u32,
    #[serde(default)]
    pub output_tokens: u32,
    #[serde(default)]
    pub cache_read: u32,
    #[serde(default)]
    pub cache_create: u32,
    #[serde(default)]
    pub reasoning_tokens: u32,
}

impl Usage {
    pub fn total(&self) -> u32 {
        self.input_tokens
            .saturating_add(self.output_tokens)
            .saturating_add(self.reasoning_tokens)
    }
}

// -----------------------------------------------------------------------------
// 错误
// -----------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum LlmError {
    #[error("http error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("oauth error: {0}")]
    Oauth(String),
    #[error("authentication failed: {0}")]
    Auth(String),
    #[error("provider error ({status}): {body}")]
    Provider { status: u16, body: String },
    #[error("stream parse error: {0}")]
    Stream(String),
    #[error("unsupported feature: {0}")]
    Unsupported(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("config error: {0}")]
    Config(String),
    #[error("{0}")]
    Other(String),
}

impl From<anyhow::Error> for LlmError {
    fn from(e: anyhow::Error) -> Self {
        LlmError::Other(e.to_string())
    }
}

// -----------------------------------------------------------------------------
// Backend trait
// -----------------------------------------------------------------------------

pub type ChunkStream<'a> = Pin<Box<dyn Stream<Item = Result<ChatChunk, LlmError>> + Send + 'a>>;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct ModelInfo {
    pub id: String,
    pub display_name: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    /// 最大上下文 token 数,如果 backend 不能确切给出则为 None。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context_window: Option<u32>,
}

/// 所有 LLM provider 都实现这个 trait。
#[async_trait]
pub trait LlmBackend: Send + Sync {
    fn kind(&self) -> BackendKind;

    /// 主路径:返回一个流式 ChatChunk 序列。`req.stream=false` 时实现也可以
    /// 退化成"一次性返回一个 Text + 一个 Stop"的双元素流。
    async fn stream_chat<'a>(&'a self, req: ChatRequest) -> Result<ChunkStream<'a>, LlmError>;

    /// 罗列 backend 支持的模型。失败可返回空 Vec(避免硬编码到 catalog)。
    async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
        Ok(vec![])
    }

    /// 文本 embedding。多数 backend 不支持;Vertex / OpenAI 走自己的路径。
    async fn embed(&self, _model: &str, _texts: &[String]) -> Result<Vec<Vec<f32>>, LlmError> {
        Err(LlmError::Unsupported("embed".into()))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
#[serde(rename_all = "snake_case")]
pub enum BackendKind {
    Anthropic,
    Vertex,
    Openai,
    OpenaiCompat,
}

impl fmt::Display for BackendKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BackendKind::Anthropic => f.write_str("anthropic"),
            BackendKind::Vertex => f.write_str("vertex_ai"),
            BackendKind::Openai => f.write_str("openai"),
            BackendKind::OpenaiCompat => f.write_str("openai_compat"),
        }
    }
}

// -----------------------------------------------------------------------------
// Tool-use streaming accumulator
// -----------------------------------------------------------------------------

/// Anthropic / OpenAI 的 tool input 都是分片字符串增量,流末尾合并 JSON。
/// 这个 helper 给 backend 复用。
#[derive(Debug, Default)]
pub struct ToolCallAccumulator {
    pub id: String,
    pub name: String,
    pub buf: String,
}

impl ToolCallAccumulator {
    pub fn append(&mut self, partial: &str) {
        self.buf.push_str(partial);
    }

    /// 把累积的 partial JSON 合并 parse 成 Value;空串视为 `{}`。
    pub fn finalize(self) -> Result<serde_json::Value, LlmError> {
        let s = if self.buf.is_empty() { "{}" } else { self.buf.as_str() };
        serde_json::from_str(s).map_err(LlmError::from)
    }

    /// 不消耗 self 的 finalize,失败时仍然产出 Value::Object({})。
    pub fn finalize_lossy(&self) -> serde_json::Value {
        let s = if self.buf.is_empty() { "{}" } else { self.buf.as_str() };
        serde_json::from_str(s).unwrap_or_else(|_| serde_json::Value::Object(Default::default()))
    }
}

// -----------------------------------------------------------------------------
// 杂项 helper
// -----------------------------------------------------------------------------

/// 给所有 backend 共用的 HTTP client builder。
pub fn build_http_client(timeout_secs: u64) -> Result<reqwest::Client, LlmError> {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .user_agent("rpg-llm/0.1")
        .build()
        .map_err(LlmError::from)
}

/// 工具列表里按 `server_id__tool_name` 规则装出 namespaced name (≤64 字符)。
/// 与 Python 端 anthropic.py / vertex.py / openai_compat.py 的 sep="__" 一致。
pub fn namespaced_tool_name(server_id: &str, tool_name: &str) -> String {
    let safe_sid = sanitize_id(server_id);
    let safe_tname = sanitize_id(tool_name);
    let combined = if safe_sid.is_empty() {
        safe_tname
    } else {
        format!("{safe_sid}__{safe_tname}")
    };
    let mut s = combined;
    if s.len() > 64 {
        s.truncate(64);
    }
    s
}

fn sanitize_id(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// 切分 `server_id__tool_name`。
pub fn split_namespaced(full: &str) -> (String, String) {
    if let Some(pos) = full.find("__") {
        (full[..pos].to_string(), full[pos + 2..].to_string())
    } else {
        (String::new(), full.to_string())
    }
}

// -----------------------------------------------------------------------------
// 前端 SSE wire 形态
// -----------------------------------------------------------------------------

/// 前端 SSE 流接收的 chunk wire 形态(命名字段,ts-rs 友好)。
///
/// `ChatChunk` 是 backend 内部 enum,含 newtype tuple variant(`Text(String)`
/// 等),`#[serde(tag)]` + tuple 在 ts-rs / TS 上无法生成可用类型。本结构是
/// route 层在序列化为 SSE `chunk` event payload 前的 typed wrapper。
///
/// `kind` 取 `text` / `thinking` / `tool_call` / `usage` / `stop` / `error`,
/// 对应字段按需可空。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct WireChatChunk {
    /// chunk 子类型 tag。
    pub kind: String,
    /// `text` / `thinking` / `error` 类:实际文本内容。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    /// `tool_call` 类:已合并完的 tool 调用 id。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// `tool_call` 类:tool 名称。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    /// `tool_call` 类:已合并完的 tool input(JSON object)。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_input: Option<serde_json::Value>,
    /// `usage` 类:token 使用统计。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub usage: Option<Usage>,
    /// `stop` 类:模型给出的停止原因。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stop_reason: Option<String>,
}

impl WireChatChunk {
    /// 从内部 `ChatChunk` 投影到 wire 形态(无损,但字段拍平)。
    pub fn from_chunk(chunk: &ChatChunk) -> Self {
        match chunk {
            ChatChunk::Text(t) => Self {
                kind: "text".into(),
                text: Some(t.clone()),
                ..Default::default()
            },
            ChatChunk::Thinking(t) => Self {
                kind: "thinking".into(),
                text: Some(t.clone()),
                ..Default::default()
            },
            ChatChunk::ToolCall { id, name, input } => Self {
                kind: "tool_call".into(),
                tool_call_id: Some(id.clone()),
                tool_name: Some(name.clone()),
                tool_input: Some(input.clone()),
                ..Default::default()
            },
            ChatChunk::Usage(u) => Self {
                kind: "usage".into(),
                usage: Some(*u),
                ..Default::default()
            },
            ChatChunk::Stop { reason } => Self {
                kind: "stop".into(),
                stop_reason: Some(reason.clone()),
                ..Default::default()
            },
            ChatChunk::Error(e) => Self {
                kind: "error".into(),
                text: Some(e.clone()),
                ..Default::default()
            },
        }
    }
}

/// 构造一份 backend 不可知的 extended thinking `extra` patch。
///
/// Wave 10-A:统一抽象 thinking/reasoning 三个 provider 的开启方式。
/// caller(rpg-routes / rpg-agents)把这个 Value 直接赋给 `ChatRequest::extra`,
/// 各 backend 各取所需:
///   * Anthropic / Vertex 看 `thinking_budget`(token 数)
///   * OpenAI / Responses 看 `reasoning_effort`(`low` / `medium` / `high`)
///
/// `budget=0` 返回 `Value::Null`,caller 可直接挂上去,等同 "不启用 thinking"
/// (各 backend 在 build_body 时把 thinking 关掉)。
///
/// 阈值映射(token → effort):0=off, 1..=2000=low, 2001..=5000=medium, >5000=high。
/// 这是粗略心智模型,与 OpenAI/o-系列文档"effort 大致对应每 step 思考 token 上限"
/// 的实证一致(o3-mini high ≈ 8k+ thoughts)。
pub fn build_thinking_extra(budget: u32) -> serde_json::Value {
    if budget == 0 {
        return serde_json::Value::Null;
    }
    let effort = if budget <= 2000 {
        "low"
    } else if budget <= 5000 {
        "medium"
    } else {
        "high"
    };
    serde_json::json!({
        "thinking_budget": budget,
        "reasoning_effort": effort,
    })
}

/// 把 `build_thinking_extra` 的结果合并进现有的 `ChatRequest::extra`。
///
/// 既有 `extra`(对象 / Null / 其它形态)与 thinking patch 浅合并:
///   * 既有为 Null → 直接用 patch(patch 也是 Null 时 extra 保持 Null)
///   * 既有为 Object → patch 字段写入(已存在则不覆盖,保留 caller 显式 override)
///   * 既有为非 Object 非 Null → 不动(避免破坏未知 schema)
///
/// 注:不 deep-merge,只在顶层合并(thinking 的字段都是顶层标量,无需递归)。
pub fn merge_thinking_extra(extra: &mut serde_json::Value, budget: u32) {
    let patch = build_thinking_extra(budget);
    if patch.is_null() {
        return;
    }
    let Some(patch_obj) = patch.as_object() else {
        return;
    };
    if extra.is_null() {
        *extra = serde_json::Value::Object(patch_obj.clone());
        return;
    }
    let Some(obj) = extra.as_object_mut() else {
        return;
    };
    for (k, v) in patch_obj {
        obj.entry(k.clone()).or_insert_with(|| v.clone());
    }
}

/// 为 backend 提供的简单 header bag。
pub fn extra_headers(extra: &serde_json::Value, key: &str) -> Option<HashMap<String, String>> {
    let m = extra.as_object()?.get(key)?.as_object()?;
    let mut out = HashMap::new();
    for (k, v) in m {
        if let Some(s) = v.as_str() {
            out.insert(k.clone(), s.to_string());
        }
    }
    Some(out)
}

#[cfg(test)]
mod ts_export_tests {
    /// 触发 ts-rs 导出(--features ts-rs 时生效)。
    #[cfg(feature = "ts-rs")]
    #[test]
    fn export_ts_types() {
        // ts-rs 在 #[ts(export)] 时会通过 inventory/ctor 机制在测试结束后自动写文件。
    }
}

#[cfg(test)]
mod thinking_extra_tests {
    use super::*;

    #[test]
    fn test_build_thinking_extra_zero_is_null() {
        assert_eq!(build_thinking_extra(0), serde_json::Value::Null);
    }

    #[test]
    fn test_build_thinking_extra_maps_effort_buckets() {
        // 低预算 → low
        let v = build_thinking_extra(1000);
        assert_eq!(v["thinking_budget"], 1000);
        assert_eq!(v["reasoning_effort"], "low");

        // 中预算 → medium
        let v = build_thinking_extra(3000);
        assert_eq!(v["thinking_budget"], 3000);
        assert_eq!(v["reasoning_effort"], "medium");

        // 高预算 → high
        let v = build_thinking_extra(8000);
        assert_eq!(v["thinking_budget"], 8000);
        assert_eq!(v["reasoning_effort"], "high");
    }

    #[test]
    fn test_merge_thinking_extra_into_null_extra() {
        let mut extra = serde_json::Value::Null;
        merge_thinking_extra(&mut extra, 4000);
        assert_eq!(extra["thinking_budget"], 4000);
        assert_eq!(extra["reasoning_effort"], "medium");
    }

    #[test]
    fn test_merge_thinking_extra_into_existing_object_does_not_overwrite() {
        let mut extra = serde_json::json!({
            "reasoning_effort": "high",  // caller 显式覆盖,不应被 patch 改写
            "metadata": {"user": "u1"},
        });
        merge_thinking_extra(&mut extra, 1500); // 1500 → low
        assert_eq!(
            extra["reasoning_effort"], "high",
            "caller-supplied effort must take precedence"
        );
        assert_eq!(extra["thinking_budget"], 1500);
        assert_eq!(extra["metadata"]["user"], "u1");
    }

    #[test]
    fn test_merge_thinking_extra_zero_is_noop() {
        let mut extra = serde_json::json!({"foo": "bar"});
        merge_thinking_extra(&mut extra, 0);
        assert_eq!(extra, serde_json::json!({"foo": "bar"}));
    }

    /// 投影 ChatChunk::Thinking → WireChatChunk 必须给前端可识别的 kind="thinking"。
    #[test]
    fn test_wire_chunk_thinking_projection() {
        let chunk = ChatChunk::Thinking("(在想)".into());
        let wire = WireChatChunk::from_chunk(&chunk);
        assert_eq!(wire.kind, "thinking");
        assert_eq!(wire.text.as_deref(), Some("(在想)"));
    }
}
