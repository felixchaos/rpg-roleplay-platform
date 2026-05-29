//! anthropic — Messages API + SSE 流 + tool_use 增量 JSON 状态机。
//!
//! 对应 Python `rpg/agents/gm/backends/anthropic.py`。
//! 端点:`POST https://api.anthropic.com/v1/messages`
//! Header:`x-api-key`, `anthropic-version: 2023-06-01`,可选 `anthropic-beta`。
//!
//! SSE 事件:
//!   message_start            → 携带初始 usage.input_tokens
//!   content_block_start      → 标记开始 text 块或 tool_use 块
//!   content_block_delta      → text_delta / input_json_delta
//!   content_block_stop       → 关闭当前块,tool_use 时这里合并 partial_json -> ToolCall
//!   message_delta            → 携带 stop_reason 与最终 usage.output_tokens
//!   message_stop             → 流末
//!
//! 该实现完整覆盖以上,生成统一 ChatChunk 序列。

use std::collections::HashMap;

use async_trait::async_trait;
use eventsource_stream::Eventsource;
use futures_util::stream::{self, StreamExt, TryStreamExt};
use serde::{Deserialize, Serialize};
use smallvec::SmallVec;

use crate::pipeline::{
    build_http_client, namespaced_tool_name, BackendKind, ChatChunk, ChatMessage, ChatRequest,
    ChatRole, ChunkStream, LlmBackend, LlmError, MessagePart, ModelInfo, ToolCallAccumulator,
    Usage,
};

const DEFAULT_BASE_URL: &str = "https://api.anthropic.com";
const ANTHROPIC_VERSION: &str = "2023-06-01";

pub struct AnthropicBackend {
    api_key: String,
    base_url: String,
    http: reqwest::Client,
    /// 可选 anthropic-beta header (e.g. "prompt-caching-2024-07-31,messages-2023-12-15")。
    beta: Option<String>,
}

impl AnthropicBackend {
    pub fn new(api_key: impl Into<String>) -> Result<Self, LlmError> {
        Self::with_base_url(api_key, DEFAULT_BASE_URL)
    }

    pub fn with_base_url(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, LlmError> {
        Ok(Self {
            api_key: api_key.into(),
            base_url: base_url.into(),
            http: build_http_client(600)?,
            beta: None,
        })
    }

    pub fn with_beta(mut self, beta: impl Into<String>) -> Self {
        self.beta = Some(beta.into());
        self
    }

    fn build_body(&self, req: &ChatRequest) -> Result<serde_json::Value, LlmError> {
        let mut body = serde_json::Map::new();
        body.insert("model".into(), serde_json::Value::String(req.model.clone()));
        // 服务端硬 clamp:忽略客户端超限的 max_tokens,防刷爆。
        // 贵模型(opus)后续可在此处加授权位检查再放宽上限。
        // TODO[auth]: if req.model.contains("opus") && !authorized_for_premium { 维持低上限 }
        let max_tokens = req
            .max_tokens
            .unwrap_or(4096)
            .min(crate::HARD_MAX_OUTPUT_TOKENS);
        body.insert(
            "max_tokens".into(),
            serde_json::Value::Number(max_tokens.into()),
        );
        if req.stream {
            body.insert("stream".into(), serde_json::Value::Bool(true));
        }
        if let Some(sys) = &req.system {
            // system 可以是 string 或 list of {text, cache_control}。
            // 从 req.extra.system_cache_control 决定是否包装。
            if let Some(cc) = req.extra.get("system_cache_control").cloned() {
                body.insert(
                    "system".into(),
                    serde_json::json!([
                        {
                            "type": "text",
                            "text": sys,
                            "cache_control": cc,
                        }
                    ]),
                );
            } else {
                body.insert("system".into(), serde_json::Value::String(sys.clone()));
            }
        }
        if let Some(t) = req.temperature {
            body.insert("temperature".into(), serde_json::json!(t));
        }
        if !req.tools.is_empty() {
            let tools: Vec<serde_json::Value> = req
                .tools
                .iter()
                .map(|t| {
                    let mut schema = t.input_schema.clone();
                    // 强制顶层 type=object,否则 Anthropic 直接 400。
                    if !schema.is_object()
                        || schema.get("type").and_then(|v| v.as_str()) != Some("object")
                    {
                        schema = serde_json::json!({"type": "object", "properties": {}});
                    }
                    let full_name = match &t.server_id {
                        Some(sid) => namespaced_tool_name(sid, &t.name),
                        None => t.name.clone(),
                    };
                    serde_json::json!({
                        "name": full_name,
                        "description": clip(&t.description, 512),
                        "input_schema": schema,
                    })
                })
                .collect();
            body.insert("tools".into(), serde_json::Value::Array(tools));
            body.insert(
                "tool_choice".into(),
                req.extra
                    .get("tool_choice")
                    .cloned()
                    .unwrap_or_else(|| serde_json::json!({"type": "auto"})),
            );
        }

        body.insert("messages".into(), messages_to_anthropic(&req.messages)?);

        // Extended thinking。两种触发方式:
        //   1. req.extra.thinking 直接给 {type:"enabled", budget_tokens: N}
        //   2. req.extra.thinking_budget = N (与 Vertex 对齐的短写法)
        if let Some(t) = req.extra.get("thinking") {
            body.insert("thinking".into(), t.clone());
        } else if let Some(budget) = req.extra.get("thinking_budget") {
            body.insert(
                "thinking".into(),
                serde_json::json!({
                    "type": "enabled",
                    "budget_tokens": budget,
                }),
            );
        }

        // 透传其它 extra 字段 (e.g. metadata, top_p, top_k, stop_sequences)
        if let Some(obj) = req.extra.as_object() {
            for (k, v) in obj {
                if matches!(
                    k.as_str(),
                    "system_cache_control"
                        | "tool_choice"
                        | "headers"
                        | "thinking"
                        | "thinking_budget"
                ) {
                    continue;
                }
                body.insert(k.clone(), v.clone());
            }
        }
        Ok(serde_json::Value::Object(body))
    }

    fn build_headers(&self, req: &ChatRequest) -> reqwest::header::HeaderMap {
        use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
        let mut h = HeaderMap::new();
        h.insert(
            "x-api-key",
            HeaderValue::from_str(&self.api_key).unwrap_or(HeaderValue::from_static("invalid")),
        );
        h.insert(
            "anthropic-version",
            HeaderValue::from_static(ANTHROPIC_VERSION),
        );
        h.insert(
            reqwest::header::CONTENT_TYPE,
            HeaderValue::from_static("application/json"),
        );
        if let Some(beta) = &self.beta {
            if let Ok(v) = HeaderValue::from_str(beta) {
                h.insert("anthropic-beta", v);
            }
        }
        // request 内 override
        if let Some(extra_headers) = crate::pipeline::extra_headers(&req.extra, "headers") {
            for (k, v) in extra_headers {
                if let (Ok(name), Ok(val)) = (
                    HeaderName::try_from(k.as_str()),
                    HeaderValue::from_str(&v),
                ) {
                    h.insert(name, val);
                }
            }
        }
        h
    }
}

#[async_trait]
impl LlmBackend for AnthropicBackend {
    fn kind(&self) -> BackendKind {
        BackendKind::Anthropic
    }

    #[tracing::instrument(skip(self, req), fields(model = %req.model, stream = req.stream))]
    async fn stream_chat<'a>(&'a self, req: ChatRequest) -> Result<ChunkStream<'a>, LlmError> {
        let body = self.build_body(&req)?;
        let headers = self.build_headers(&req);
        let url = format!("{}/v1/messages", self.base_url.trim_end_matches('/'));
        let resp = self
            .http
            .post(&url)
            .headers(headers)
            .json(&body)
            .send()
            .await?;

        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(LlmError::Provider {
                status: status.as_u16(),
                body,
            });
        }

        if !req.stream {
            // 非流模式:直接拿 JSON,拍扁成单个 chunk + Stop。
            let payload: AnthropicNonStreamResponse = resp.json().await?;
            let mut out: Vec<Result<ChatChunk, LlmError>> = Vec::new();
            for block in payload.content {
                match block {
                    AnthropicContentBlock::Text { text } => {
                        out.push(Ok(ChatChunk::Text(text)));
                    }
                    AnthropicContentBlock::ToolUse { id, name, input } => {
                        out.push(Ok(ChatChunk::ToolCall { id, name, input }));
                    }
                }
            }
            if let Some(u) = payload.usage {
                out.push(Ok(ChatChunk::Usage(u.into())));
            }
            out.push(Ok(ChatChunk::Stop {
                reason: payload.stop_reason.unwrap_or_else(|| "end_turn".into()),
            }));
            return Ok(Box::pin(stream::iter(out)));
        }

        // 流式:按 SSE 解析,状态机驱动。
        let event_stream = resp
            .bytes_stream()
            .map_err(std::io::Error::other)
            .eventsource()
            .map_err(|e| LlmError::Stream(e.to_string()));

        let state = StreamState::default();
        let parsed = event_stream.scan(state, |state, ev_res| {
            let chunks: SseChunks = match ev_res {
                Ok(ev) => state.process(&ev.event, &ev.data),
                Err(e) => {
                    let mut sv: SseChunks = SmallVec::new();
                    sv.push(Err(e));
                    sv
                }
            };
            futures_util::future::ready(Some(chunks))
        });

        let flat = parsed.flat_map(stream::iter);
        Ok(Box::pin(flat))
    }

    #[tracing::instrument(skip(self))]
    async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
        // Anthropic 公开了 GET /v1/models 端点。失败则降级到 hardcoded list。
        let url = format!("{}/v1/models", self.base_url.trim_end_matches('/'));
        let resp = self
            .http
            .get(&url)
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", ANTHROPIC_VERSION)
            .send()
            .await;
        if let Ok(r) = resp {
            if r.status().is_success() {
                if let Ok(v) = r.json::<serde_json::Value>().await {
                    if let Some(arr) = v.get("data").and_then(|d| d.as_array()) {
                        let models = arr
                            .iter()
                            .filter_map(|m| {
                                let id = m.get("id")?.as_str()?.to_string();
                                let display = m
                                    .get("display_name")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or(&id)
                                    .to_string();
                                Some(ModelInfo {
                                    id,
                                    display_name: display,
                                    capabilities: vec![
                                        "text".into(),
                                        "streaming".into(),
                                        "tools".into(),
                                    ],
                                    context_window: Some(200_000),
                                })
                            })
                            .collect();
                        return Ok(models);
                    }
                }
            }
        }
        Ok(default_anthropic_models())
    }
}

fn default_anthropic_models() -> Vec<ModelInfo> {
    let ids = [
        ("claude-opus-4-7", "Claude Opus 4.7"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ];
    ids.iter()
        .map(|(id, name)| ModelInfo {
            id: (*id).to_string(),
            display_name: (*name).to_string(),
            capabilities: vec!["text".into(), "streaming".into(), "tools".into()],
            context_window: Some(200_000),
        })
        .collect()
}

// -----------------------------------------------------------------------------
// SSE 状态机
// -----------------------------------------------------------------------------

#[derive(Debug, Default)]
struct StreamState {
    /// content block index → 累加器 (tool_use 期)。
    current_tool: Option<ToolCallAccumulator>,
    /// 暂存 message-level usage 增量。
    usage: Usage,
    stop_reason: Option<String>,
    /// 当前 thinking block 的累积签名(`signature_delta` 增量,流末做完整性
    /// 校验时使用)。`None` 表示当前不在 thinking 块内。
    current_thinking_signature: Option<String>,
}

// SSE 每事件通常只产出 0-1 个 chunk;2 槽足够 message_stop(Usage+Stop)栈上存放。
type SseChunks = SmallVec<[Result<ChatChunk, LlmError>; 2]>;

impl StreamState {
    fn process(&mut self, event: &str, data: &str) -> SseChunks {
        // 解析 JSON,失败直接吐 error chunk 但不停止流。
        // hot path: 使用 simd-json;fallback serde_json (在 simd_parse 内部自动处理)。
        let value: serde_json::Value = match crate::simd_parse::parse_sse_value(data) {
            Ok(v) => v,
            Err(e) => {
                let mut sv: SseChunks = SmallVec::new();
                sv.push(Ok(ChatChunk::Error(format!(
                    "anthropic sse parse: {e}; event={event}; data={}",
                    clip(data, 200)
                ))));
                return sv;
            }
        };
        let mut out: SseChunks = SmallVec::new();
        match event {
            "message_start" => {
                if let Some(u) = value
                    .get("message")
                    .and_then(|m| m.get("usage"))
                    .and_then(|u| serde_json::from_value::<AnthropicUsage>(u.clone()).ok())
                {
                    self.usage.input_tokens = u.input_tokens.unwrap_or(0);
                    self.usage.cache_read = u.cache_read_input_tokens.unwrap_or(0);
                    self.usage.cache_create = u.cache_creation_input_tokens.unwrap_or(0);
                }
            }
            "content_block_start" => {
                let bt = value
                    .get("content_block")
                    .and_then(|b| b.get("type"))
                    .and_then(|t| t.as_str());
                match bt {
                    Some("tool_use") => {
                        let id = value
                            .get("content_block")
                            .and_then(|b| b.get("id"))
                            .and_then(|v| v.as_str())
                            .unwrap_or_default()
                            .to_string();
                        let name = value
                            .get("content_block")
                            .and_then(|b| b.get("name"))
                            .and_then(|v| v.as_str())
                            .unwrap_or_default()
                            .to_string();
                        self.current_tool = Some(ToolCallAccumulator {
                            id,
                            name,
                            buf: String::new(),
                        });
                    }
                    Some("thinking") => {
                        // 进入 extended thinking 块,初始化 signature 累加器。
                        self.current_thinking_signature = Some(String::new());
                    }
                    _ => {}
                }
            }
            "content_block_delta" => {
                let dt = value.get("delta").and_then(|d| d.get("type")).and_then(|t| t.as_str());
                match dt {
                    Some("text_delta") => {
                        if let Some(text) = value
                            .get("delta")
                            .and_then(|d| d.get("text"))
                            .and_then(|t| t.as_str())
                        {
                            if !text.is_empty() {
                                out.push(Ok(ChatChunk::Text(text.to_string())));
                            }
                        }
                    }
                    Some("input_json_delta") => {
                        if let Some(p) = value
                            .get("delta")
                            .and_then(|d| d.get("partial_json"))
                            .and_then(|t| t.as_str())
                        {
                            if let Some(acc) = self.current_tool.as_mut() {
                                acc.append(p);
                            }
                        }
                    }
                    Some("thinking_delta") => {
                        // Extended thinking 文本流;直接 emit 一个 Thinking chunk。
                        if let Some(thinking) = value
                            .get("delta")
                            .and_then(|d| d.get("thinking"))
                            .and_then(|t| t.as_str())
                        {
                            if !thinking.is_empty() {
                                out.push(Ok(ChatChunk::Thinking(thinking.to_string())));
                            }
                        }
                    }
                    Some("signature_delta") => {
                        // thinking block 的密码学签名,保留到 block 末尾。
                        if let Some(sig) = value
                            .get("delta")
                            .and_then(|d| d.get("signature"))
                            .and_then(|t| t.as_str())
                        {
                            if let Some(acc) = self.current_thinking_signature.as_mut() {
                                acc.push_str(sig);
                            } else {
                                self.current_thinking_signature = Some(sig.to_string());
                            }
                        }
                    }
                    _ => { /* 未知 delta 类型直接忽略 */ }
                }
            }
            "content_block_stop" => {
                if let Some(acc) = self.current_tool.take() {
                    let id = acc.id.clone();
                    let name = acc.name.clone();
                    let input = acc.finalize_lossy();
                    out.push(Ok(ChatChunk::ToolCall { id, name, input }));
                }
                // thinking block 结束时:签名收尾(目前不向上 emit,后续若
                // 需要原样回填 thinking block 时由调用方再取)。
                self.current_thinking_signature = None;
            }
            "message_delta" => {
                if let Some(sr) = value
                    .get("delta")
                    .and_then(|d| d.get("stop_reason"))
                    .and_then(|v| v.as_str())
                {
                    self.stop_reason = Some(sr.to_string());
                }
                if let Some(u) = value
                    .get("usage")
                    .and_then(|u| serde_json::from_value::<AnthropicUsage>(u.clone()).ok())
                {
                    if let Some(o) = u.output_tokens {
                        self.usage.output_tokens = o;
                    }
                    if let Some(c) = u.cache_read_input_tokens {
                        self.usage.cache_read = c;
                    }
                }
            }
            "message_stop" => {
                out.push(Ok(ChatChunk::Usage(self.usage)));
                out.push(Ok(ChatChunk::Stop {
                    reason: self.stop_reason.clone().unwrap_or_else(|| "end_turn".into()),
                }));
            }
            "ping" | "" => { /* keepalive */ }
            "error" => {
                let msg = value
                    .get("error")
                    .and_then(|e| e.get("message"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("anthropic stream error")
                    .to_string();
                out.push(Ok(ChatChunk::Error(msg)));
            }
            _ => { /* 未知事件忽略 */ }
        }
        out
    }
}

// -----------------------------------------------------------------------------
// 消息序列化 (统一 ChatMessage -> Anthropic 风格)
// -----------------------------------------------------------------------------

fn messages_to_anthropic(messages: &[ChatMessage]) -> Result<serde_json::Value, LlmError> {
    let mut out = Vec::new();
    for m in messages {
        // Anthropic 只接受 user / assistant;system 在外层。
        let role = match m.role {
            ChatRole::User | ChatRole::Tool => "user",
            ChatRole::Assistant => "assistant",
            ChatRole::System => continue, // 直接跳过,system 在外层传
        };
        let content = message_content_to_anthropic(m);
        out.push(serde_json::json!({ "role": role, "content": content }));
    }
    Ok(serde_json::Value::Array(out))
}

fn message_content_to_anthropic(m: &ChatMessage) -> serde_json::Value {
    // tool 角色 → 装成 user 的 [{type: tool_result, ...}] block。
    if matches!(m.role, ChatRole::Tool) {
        return serde_json::json!([
            {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id.clone().unwrap_or_default(),
                "content": m.content.clone().unwrap_or_default(),
            }
        ]);
    }
    // assistant 包含 tool_calls → 拼 multipart。
    if matches!(m.role, ChatRole::Assistant) && !m.tool_calls.is_empty() {
        let mut blocks = Vec::new();
        if let Some(text) = &m.content {
            if !text.is_empty() {
                blocks.push(serde_json::json!({"type": "text", "text": text}));
            }
        }
        for tc in &m.tool_calls {
            blocks.push(serde_json::json!({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }));
        }
        return serde_json::Value::Array(blocks);
    }
    // 有 multipart parts → 各自转换。
    if !m.parts.is_empty() {
        let mut blocks = Vec::new();
        for p in &m.parts {
            match p {
                MessagePart::Text { text } => {
                    blocks.push(serde_json::json!({"type": "text", "text": text}));
                }
                MessagePart::Image { source, media_type } => {
                    blocks.push(serde_json::json!({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type.clone().unwrap_or_else(|| "image/png".into()),
                            "data": source,
                        }
                    }));
                }
                MessagePart::FileData { mime_type, file_uri } => {
                    // Anthropic Files API:走 source.type=url。
                    // (audio/video 目前不被 Anthropic 接受,backend 由 GameMaster 层
                    // 决定是否传入;这里只做转译。)
                    blocks.push(serde_json::json!({
                        "type": "image",
                        "source": {
                            "type": "url",
                            "media_type": mime_type,
                            "url": file_uri,
                        }
                    }));
                }
                MessagePart::InlineData { mime_type, data } => {
                    use base64::Engine;
                    let encoded = base64::engine::general_purpose::STANDARD.encode(data);
                    blocks.push(serde_json::json!({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": encoded,
                        }
                    }));
                }
                MessagePart::ToolUse { id, name, input } => {
                    blocks.push(serde_json::json!({
                        "type": "tool_use",
                        "id": id,
                        "name": name,
                        "input": input,
                    }));
                }
                MessagePart::ToolResult { tool_use_id, content, is_error } => {
                    blocks.push(serde_json::json!({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                        "is_error": is_error,
                    }));
                }
            }
        }
        return serde_json::Value::Array(blocks);
    }
    // 普通 text
    serde_json::Value::String(m.content.clone().unwrap_or_default())
}

// -----------------------------------------------------------------------------
// 非流 response 结构
// -----------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct AnthropicNonStreamResponse {
    #[serde(default)]
    content: Vec<AnthropicContentBlock>,
    #[serde(default)]
    stop_reason: Option<String>,
    #[serde(default)]
    usage: Option<AnthropicUsage>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum AnthropicContentBlock {
    Text {
        text: String,
    },
    ToolUse {
        id: String,
        name: String,
        input: serde_json::Value,
    },
}

#[derive(Debug, Deserialize, Default, Clone)]
struct AnthropicUsage {
    #[serde(default)]
    input_tokens: Option<u32>,
    #[serde(default)]
    output_tokens: Option<u32>,
    #[serde(default)]
    cache_read_input_tokens: Option<u32>,
    #[serde(default)]
    cache_creation_input_tokens: Option<u32>,
}

impl From<AnthropicUsage> for Usage {
    fn from(u: AnthropicUsage) -> Self {
        Usage {
            input_tokens: u.input_tokens.unwrap_or(0),
            output_tokens: u.output_tokens.unwrap_or(0),
            cache_read: u.cache_read_input_tokens.unwrap_or(0),
            cache_create: u.cache_creation_input_tokens.unwrap_or(0),
            reasoning_tokens: 0,
        }
    }
}

fn clip(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        s.chars().take(n).collect::<String>() + "…"
    }
}

// 给 future 兼容,保留一个空导出避免 unused 警告。
#[allow(dead_code)]
fn _ensure_hashmap_import() -> HashMap<String, String> {
    HashMap::new()
}

// -----------------------------------------------------------------------------
// SSE state machine 单元测试
// -----------------------------------------------------------------------------
//
// fixture JSON 直接抄自 Anthropic Messages API SSE 规范
// (https://docs.anthropic.com/en/api/messages-streaming),驱动 StreamState
// 的 6 条核心路径:basic text / tool_use partial json / extended thinking /
// 多 block 顺序 / usage 透传 / stop_reason 透传。
#[cfg(test)]
mod tests {
    use super::*;
    use crate::pipeline::ChatChunk;

    /// 把一组 (event, data) 顺次喂给一个新 StreamState,聚合所有 emit。
    fn drive(events: &[(&str, &str)]) -> Vec<ChatChunk> {
        let mut state = StreamState::default();
        let mut out = Vec::new();
        for (ev, data) in events {
            for r in state.process(ev, data) {
                out.push(r.expect("state machine should not emit hard errors on valid fixture"));
            }
        }
        out
    }

    fn count_text(chunks: &[ChatChunk]) -> usize {
        chunks
            .iter()
            .filter(|c| matches!(c, ChatChunk::Text(_)))
            .count()
    }

    fn last_stop_reason(chunks: &[ChatChunk]) -> Option<String> {
        chunks.iter().rev().find_map(|c| match c {
            ChatChunk::Stop { reason } => Some(reason.clone()),
            _ => None,
        })
    }

    fn last_usage(chunks: &[ChatChunk]) -> Option<Usage> {
        chunks.iter().rev().find_map(|c| match c {
            ChatChunk::Usage(u) => Some(*u),
            _ => None,
        })
    }

    // --------- test 1: 基本 text 流 ---------
    #[test]
    fn test_basic_text_streaming() {
        let events: &[(&str, &str)] = &[
            (
                "message_start",
                r#"{"type":"message_start","message":{"id":"msg_01","type":"message","role":"assistant","content":[],"model":"claude-opus-4-7","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":12,"output_tokens":1}}}"#,
            ),
            (
                "content_block_start",
                r#"{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}"#,
            ),
            (
                "content_block_stop",
                r#"{"type":"content_block_stop","index":0}"#,
            ),
            (
                "message_delta",
                r#"{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":24}}"#,
            ),
            (
                "message_stop",
                r#"{"type":"message_stop"}"#,
            ),
        ];

        let chunks = drive(events);
        // 3 个 text + 1 个 usage + 1 个 stop
        assert_eq!(count_text(&chunks), 3, "expect 3 text chunks, got {:?}", chunks);
        for c in chunks.iter().filter(|c| matches!(c, ChatChunk::Text(_))) {
            if let ChatChunk::Text(t) = c {
                assert_eq!(t, "Hello");
            }
        }
        assert_eq!(last_stop_reason(&chunks).as_deref(), Some("end_turn"));
        let u = last_usage(&chunks).expect("expect Usage chunk");
        assert_eq!(u.input_tokens, 12);
        assert_eq!(u.output_tokens, 24);
    }

    // --------- test 2: tool_use partial_json 累加 ---------
    #[test]
    fn test_tool_use_partial_json_accumulation() {
        let events: &[(&str, &str)] = &[
            (
                "message_start",
                r#"{"type":"message_start","message":{"id":"msg_02","type":"message","role":"assistant","content":[],"model":"claude-opus-4-7","stop_reason":null,"usage":{"input_tokens":5,"output_tokens":1}}}"#,
            ),
            (
                "content_block_start",
                r#"{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_X","name":"calc_tool","input":{}}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"a\":"}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"1}"}}"#,
            ),
            (
                "content_block_stop",
                r#"{"type":"content_block_stop","index":0}"#,
            ),
            (
                "message_delta",
                r#"{"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":7}}"#,
            ),
            (
                "message_stop",
                r#"{"type":"message_stop"}"#,
            ),
        ];
        let chunks = drive(events);

        // 必须有且仅有 1 个 ToolCall;input 是合并完的 {"a":1}
        let tool_calls: Vec<&ChatChunk> = chunks
            .iter()
            .filter(|c| matches!(c, ChatChunk::ToolCall { .. }))
            .collect();
        assert_eq!(tool_calls.len(), 1, "expect single ToolCall, got {:?}", chunks);
        match tool_calls[0] {
            ChatChunk::ToolCall { id, name, input } => {
                assert_eq!(id, "toolu_X");
                assert_eq!(name, "calc_tool");
                assert_eq!(input, &serde_json::json!({"a": 1}));
            }
            _ => unreachable!(),
        }
        assert_eq!(last_stop_reason(&chunks).as_deref(), Some("tool_use"));
    }

    // --------- test 3: extended thinking + signature_delta ---------
    #[test]
    fn test_extended_thinking() {
        let events: &[(&str, &str)] = &[
            (
                "message_start",
                r#"{"type":"message_start","message":{"id":"msg_03","type":"message","role":"assistant","content":[],"model":"claude-opus-4-7","stop_reason":null,"usage":{"input_tokens":8,"output_tokens":1}}}"#,
            ),
            (
                "content_block_start",
                r#"{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"reasoning"}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig123"}}"#,
            ),
            (
                "content_block_stop",
                r#"{"type":"content_block_stop","index":0}"#,
            ),
            (
                "message_delta",
                r#"{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":3}}"#,
            ),
            (
                "message_stop",
                r#"{"type":"message_stop"}"#,
            ),
        ];

        // 这里我们要观察 signature_delta 的内部累加,直接驱动一个 state 检视
        let mut state = StreamState::default();
        let mut chunks = Vec::new();
        for (i, (ev, data)) in events.iter().enumerate() {
            for r in state.process(ev, data) {
                chunks.push(r.expect("no hard errors"));
            }
            // signature_delta 出现后 (index=3) 且 content_block_stop 之前 (index=4),
            // 累积应等于 "sig123"
            if i == 3 {
                assert_eq!(
                    state.current_thinking_signature.as_deref(),
                    Some("sig123"),
                    "signature should accumulate inside thinking block",
                );
            }
            // content_block_stop 之后,thinking signature 状态被清空
            if i == 4 {
                assert!(
                    state.current_thinking_signature.is_none(),
                    "signature buffer must be cleared on content_block_stop",
                );
            }
        }

        let thinking_chunks: Vec<&ChatChunk> = chunks
            .iter()
            .filter(|c| matches!(c, ChatChunk::Thinking(_)))
            .collect();
        assert_eq!(thinking_chunks.len(), 1);
        if let ChatChunk::Thinking(t) = thinking_chunks[0] {
            assert_eq!(t, "reasoning");
        }
        // signature_delta 不向上 emit 任何 ChatChunk
        assert_eq!(count_text(&chunks), 0);
        assert_eq!(last_stop_reason(&chunks).as_deref(), Some("end_turn"));
    }

    // --------- test 4: 多 content_block 顺序 (text + text + tool_use) ---------
    #[test]
    fn test_multi_content_block() {
        let events: &[(&str, &str)] = &[
            (
                "message_start",
                r#"{"type":"message_start","message":{"id":"msg_04","type":"message","role":"assistant","content":[],"model":"claude-opus-4-7","stop_reason":null,"usage":{"input_tokens":10,"output_tokens":1}}}"#,
            ),
            // block 0: text
            (
                "content_block_start",
                r#"{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"first"}}"#,
            ),
            (
                "content_block_stop",
                r#"{"type":"content_block_stop","index":0}"#,
            ),
            // block 1: text
            (
                "content_block_start",
                r#"{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"second"}}"#,
            ),
            (
                "content_block_stop",
                r#"{"type":"content_block_stop","index":1}"#,
            ),
            // block 2: tool_use
            (
                "content_block_start",
                r#"{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"toolu_Y","name":"fetch_thing","input":{}}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"k\":\"v\"}"}}"#,
            ),
            (
                "content_block_stop",
                r#"{"type":"content_block_stop","index":2}"#,
            ),
            (
                "message_delta",
                r#"{"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":15}}"#,
            ),
            (
                "message_stop",
                r#"{"type":"message_stop"}"#,
            ),
        ];

        let chunks = drive(events);
        // 期望顺序:Text("first") → Text("second") → ToolCall → Usage → Stop
        let interesting: Vec<&ChatChunk> = chunks
            .iter()
            .filter(|c| {
                matches!(
                    c,
                    ChatChunk::Text(_) | ChatChunk::ToolCall { .. } | ChatChunk::Stop { .. }
                )
            })
            .collect();
        assert_eq!(interesting.len(), 4, "got chunks: {:?}", chunks);
        match interesting[0] {
            ChatChunk::Text(t) => assert_eq!(t, "first"),
            other => panic!("expect text 'first', got {other:?}"),
        }
        match interesting[1] {
            ChatChunk::Text(t) => assert_eq!(t, "second"),
            other => panic!("expect text 'second', got {other:?}"),
        }
        match interesting[2] {
            ChatChunk::ToolCall { id, name, input } => {
                assert_eq!(id, "toolu_Y");
                assert_eq!(name, "fetch_thing");
                assert_eq!(input, &serde_json::json!({"k": "v"}));
            }
            other => panic!("expect ToolCall, got {other:?}"),
        }
        match interesting[3] {
            ChatChunk::Stop { reason } => assert_eq!(reason, "tool_use"),
            other => panic!("expect Stop, got {other:?}"),
        }
    }

    // --------- test 5: usage 抽取 (input/output/cache_*) ---------
    #[test]
    fn test_usage_extraction() {
        let events: &[(&str, &str)] = &[
            (
                "message_start",
                r#"{"type":"message_start","message":{"id":"msg_05","type":"message","role":"assistant","content":[],"model":"claude-opus-4-7","stop_reason":null,"usage":{"input_tokens":1000,"output_tokens":1,"cache_creation_input_tokens":500,"cache_read_input_tokens":200}}}"#,
            ),
            (
                "content_block_start",
                r#"{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}"#,
            ),
            (
                "content_block_delta",
                r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}"#,
            ),
            (
                "content_block_stop",
                r#"{"type":"content_block_stop","index":0}"#,
            ),
            (
                "message_delta",
                r#"{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":42,"cache_read_input_tokens":250}}"#,
            ),
            (
                "message_stop",
                r#"{"type":"message_stop"}"#,
            ),
        ];
        let chunks = drive(events);
        let u = last_usage(&chunks).expect("expect Usage in stream tail");
        assert_eq!(u.input_tokens, 1000, "input_tokens from message_start");
        assert_eq!(u.output_tokens, 42, "output_tokens from message_delta");
        assert_eq!(
            u.cache_create, 500,
            "cache_creation_input_tokens come from message_start"
        );
        // message_delta override 了 cache_read 为 250
        assert_eq!(
            u.cache_read, 250,
            "cache_read_input_tokens should be overridden by message_delta"
        );
    }

    // --------- test 6: stop_reason 各种值都透传 ---------
    #[test]
    fn test_stop_reason_passes() {
        for reason in ["end_turn", "tool_use", "max_tokens", "stop_sequence"] {
            let msg_delta = format!(
                r#"{{"type":"message_delta","delta":{{"stop_reason":"{reason}","stop_sequence":null}},"usage":{{"output_tokens":1}}}}"#,
            );
            let events: Vec<(&str, &str)> = vec![
                (
                    "message_start",
                    r#"{"type":"message_start","message":{"id":"msg_06","type":"message","role":"assistant","content":[],"model":"claude-opus-4-7","stop_reason":null,"usage":{"input_tokens":3,"output_tokens":1}}}"#,
                ),
                (
                    "content_block_start",
                    r#"{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}"#,
                ),
                (
                    "content_block_delta",
                    r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"x"}}"#,
                ),
                (
                    "content_block_stop",
                    r#"{"type":"content_block_stop","index":0}"#,
                ),
                ("message_delta", msg_delta.as_str()),
                ("message_stop", r#"{"type":"message_stop"}"#),
            ];
            let chunks = drive(&events);
            assert_eq!(
                last_stop_reason(&chunks).as_deref(),
                Some(reason),
                "stop_reason {reason} should be passed through verbatim",
            );
        }
    }
}
