//! openai — OpenAI 兼容 backend (Chat Completions / streaming)。
//!
//! 对应 Python `rpg/agents/gm/backends/openai_compat.py`。
//! Endpoint:`POST {base_url}/chat/completions`,Auth:`Authorization: Bearer ...`。
//! 流式 SSE,每条 `data:` payload 是 Chat Completion Chunk;`data: [DONE]` 表示结束。
//!
//! tool_calls 流:`choices[0].delta.tool_calls[]`,每条带 `index`,
//! `function.name` 第一片来,`function.arguments` 是分片字符串需累加。
//! `finish_reason == "tool_calls"` 时切代到工具调用。
//!
//! 设计上覆盖纯文本 + 工具调用,留 reasoning_tokens / response_format 的边角作 TODO。

use std::collections::HashMap;

use async_trait::async_trait;
use eventsource_stream::Eventsource;
use futures_util::stream::{self, StreamExt, TryStreamExt};

use crate::pipeline::{
    build_http_client, namespaced_tool_name, BackendKind, ChatChunk, ChatMessage, ChatRequest,
    ChatRole, ChunkStream, LlmBackend, LlmError, MessagePart, ModelInfo, ToolCall, Usage,
};

const DEFAULT_BASE_URL: &str = "https://api.openai.com/v1";

pub struct OpenAiBackend {
    api_key: String,
    base_url: String,
    http: reqwest::Client,
    /// 用于 catalog 显示;也作为 unsupported_tools 缓存 key 的一部分。
    pub api_id: String,
}

impl OpenAiBackend {
    pub fn new(api_key: impl Into<String>) -> Result<Self, LlmError> {
        Self::new_with(api_key, DEFAULT_BASE_URL, "openai")
    }

    pub fn new_with(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        api_id: impl Into<String>,
    ) -> Result<Self, LlmError> {
        Ok(Self {
            api_key: api_key.into(),
            base_url: base_url.into(),
            http: build_http_client(600)?,
            api_id: api_id.into(),
        })
    }

    fn build_body(&self, req: &ChatRequest) -> Result<serde_json::Value, LlmError> {
        let mut body = serde_json::Map::new();
        body.insert("model".into(), serde_json::Value::String(req.model.clone()));
        body.insert(
            "messages".into(),
            messages_to_openai(&req.system, &req.messages),
        );
        if let Some(m) = req.max_tokens {
            body.insert("max_tokens".into(), serde_json::json!(m));
        }
        if let Some(t) = req.temperature {
            body.insert("temperature".into(), serde_json::json!(t));
        }
        if req.stream {
            body.insert("stream".into(), serde_json::Value::Bool(true));
            body.insert(
                "stream_options".into(),
                serde_json::json!({"include_usage": true}),
            );
        }
        if !req.tools.is_empty() {
            let tools: Vec<serde_json::Value> = req
                .tools
                .iter()
                .map(|t| {
                    let mut params = t.input_schema.clone();
                    if !params.is_object()
                        || params.get("type").and_then(|v| v.as_str()) != Some("object")
                    {
                        params = serde_json::json!({"type": "object", "properties": {}});
                    }
                    let name = match &t.server_id {
                        Some(sid) => namespaced_tool_name(sid, &t.name),
                        None => t.name.clone(),
                    };
                    serde_json::json!({
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": t.description,
                            "parameters": params,
                        }
                    })
                })
                .collect();
            body.insert("tools".into(), serde_json::Value::Array(tools));
            body.insert(
                "tool_choice".into(),
                req.extra
                    .get("tool_choice")
                    .cloned()
                    .unwrap_or_else(|| serde_json::json!("auto")),
            );
        }
        // 透传 extra (response_format / top_p / seed / user 等)
        if let Some(obj) = req.extra.as_object() {
            for (k, v) in obj {
                if matches!(k.as_str(), "tool_choice" | "headers") {
                    continue;
                }
                body.insert(k.clone(), v.clone());
            }
        }
        Ok(serde_json::Value::Object(body))
    }
}

#[async_trait]
impl LlmBackend for OpenAiBackend {
    fn kind(&self) -> BackendKind {
        if self.base_url.starts_with("https://api.openai.com") {
            BackendKind::Openai
        } else {
            BackendKind::OpenaiCompat
        }
    }

    #[tracing::instrument(skip(self, req), fields(model = %req.model, stream = req.stream, api_id = %self.api_id))]
    async fn stream_chat<'a>(&'a self, req: ChatRequest) -> Result<ChunkStream<'a>, LlmError> {
        let body = self.build_body(&req)?;
        let url = format!("{}/chat/completions", self.base_url.trim_end_matches('/'));

        let mut builder = self
            .http
            .post(&url)
            .bearer_auth(&self.api_key)
            .header("Content-Type", "application/json");
        if let Some(headers) = crate::pipeline::extra_headers(&req.extra, "headers") {
            for (k, v) in headers {
                builder = builder.header(k, v);
            }
        }

        let resp = builder.json(&body).send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(LlmError::Provider {
                status: status.as_u16(),
                body,
            });
        }

        if !req.stream {
            let payload: serde_json::Value = resp.json().await?;
            let mut out: Vec<Result<ChatChunk, LlmError>> = Vec::new();
            if let Some(choice) = payload
                .get("choices")
                .and_then(|c| c.as_array())
                .and_then(|a| a.first())
            {
                if let Some(message) = choice.get("message") {
                    if let Some(text) = message.get("content").and_then(|c| c.as_str()) {
                        if !text.is_empty() {
                            out.push(Ok(ChatChunk::Text(text.to_string())));
                        }
                    }
                    if let Some(tcs) = message.get("tool_calls").and_then(|t| t.as_array()) {
                        for tc in tcs {
                            let id = tc.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
                            let name = tc
                                .get("function")
                                .and_then(|f| f.get("name"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_string();
                            let args_str = tc
                                .get("function")
                                .and_then(|f| f.get("arguments"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("{}");
                            let input: serde_json::Value =
                                serde_json::from_str(args_str).unwrap_or_else(|_| {
                                    serde_json::Value::Object(Default::default())
                                });
                            out.push(Ok(ChatChunk::ToolCall { id, name, input }));
                        }
                    }
                }
                if let Some(reason) = choice.get("finish_reason").and_then(|f| f.as_str()) {
                    out.push(Ok(ChatChunk::Stop {
                        reason: reason.to_string(),
                    }));
                }
            }
            if let Some(usage) = payload.get("usage") {
                out.push(Ok(ChatChunk::Usage(parse_openai_usage(usage))));
            }
            return Ok(Box::pin(stream::iter(out)));
        }

        let event_stream = resp
            .bytes_stream()
            .map_err(std::io::Error::other)
            .eventsource()
            .map_err(|e| LlmError::Stream(e.to_string()));

        let parsed = event_stream.scan(OpenAiStreamState::default(), |state, ev_res| {
            let chunks = match ev_res {
                Ok(ev) => {
                    if ev.data.trim() == "[DONE]" {
                        state.finalize()
                    } else {
                        state.process(&ev.data)
                    }
                }
                Err(e) => vec![Err(e)],
            };
            futures_util::future::ready(Some(chunks))
        });
        let flat = parsed.flat_map(stream::iter);
        Ok(Box::pin(flat))
    }

    #[tracing::instrument(skip(self), fields(api_id = %self.api_id))]
    async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
        let url = format!("{}/models", self.base_url.trim_end_matches('/'));
        let resp = self.http.get(&url).bearer_auth(&self.api_key).send().await;
        if let Ok(r) = resp {
            if r.status().is_success() {
                if let Ok(v) = r.json::<serde_json::Value>().await {
                    if let Some(arr) = v.get("data").and_then(|d| d.as_array()) {
                        return Ok(arr
                            .iter()
                            .filter_map(|m| {
                                let id = m.get("id")?.as_str()?.to_string();
                                Some(ModelInfo {
                                    display_name: id.clone(),
                                    id,
                                    capabilities: vec!["text".into(), "streaming".into()],
                                    context_window: None,
                                })
                            })
                            .collect());
                    }
                }
            }
        }
        Ok(vec![])
    }

    async fn embed(&self, model: &str, texts: &[String]) -> Result<Vec<Vec<f32>>, LlmError> {
        if texts.is_empty() {
            return Ok(vec![]);
        }
        let url = format!("{}/embeddings", self.base_url.trim_end_matches('/'));
        let body = serde_json::json!({"model": model, "input": texts});
        let resp = self
            .http
            .post(&url)
            .bearer_auth(&self.api_key)
            .header("Content-Type", "application/json")
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
        let v: serde_json::Value = resp.json().await?;
        let arr = v
            .get("data")
            .and_then(|d| d.as_array())
            .ok_or_else(|| LlmError::Stream("openai embed: no data".into()))?;
        let mut out = Vec::with_capacity(arr.len());
        for item in arr {
            let vec = item
                .get("embedding")
                .and_then(|e| e.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|n| n.as_f64().map(|f| f as f32))
                        .collect::<Vec<f32>>()
                })
                .unwrap_or_default();
            out.push(vec);
        }
        Ok(out)
    }
}

// -----------------------------------------------------------------------------
// SSE 流状态机 (OpenAI Chat Completions)
// -----------------------------------------------------------------------------

#[derive(Debug, Default)]
struct OpenAiStreamState {
    /// index → 累积器。OpenAI 工具调用按 index 分桶。
    tool_calls: HashMap<u32, ToolCallBuf>,
    last_finish: Option<String>,
    last_usage: Option<Usage>,
    /// 是否已经吐出 Stop;[DONE] 时再补一个保险。
    finalized: bool,
}

#[derive(Debug, Default)]
struct ToolCallBuf {
    id: String,
    name: String,
    args: String,
}

impl OpenAiStreamState {
    fn process(&mut self, data: &str) -> Vec<Result<ChatChunk, LlmError>> {
        if data.trim().is_empty() {
            return vec![];
        }
        let value: serde_json::Value = match serde_json::from_str(data) {
            Ok(v) => v,
            Err(e) => {
                return vec![Ok(ChatChunk::Error(format!(
                    "openai sse parse: {e}; data={}",
                    clip(data, 200)
                )))];
            }
        };
        let mut out = Vec::new();
        if let Some(usage) = value.get("usage") {
            if !usage.is_null() {
                self.last_usage = Some(parse_openai_usage(usage));
            }
        }
        if let Some(choices) = value.get("choices").and_then(|c| c.as_array()) {
            for choice in choices {
                if let Some(delta) = choice.get("delta") {
                    if let Some(text) = delta.get("content").and_then(|c| c.as_str()) {
                        if !text.is_empty() {
                            out.push(Ok(ChatChunk::Text(text.to_string())));
                        }
                    }
                    // 兼容 reasoning 模型 (DeepSeek R1 / Kimi K2 / OpenAI o-系列):
                    // delta.reasoning_content (DeepSeek) 或 delta.reasoning (OpenAI)。
                    if let Some(rt) = delta
                        .get("reasoning_content")
                        .and_then(|v| v.as_str())
                        .or_else(|| delta.get("reasoning").and_then(|v| v.as_str()))
                    {
                        if !rt.is_empty() {
                            out.push(Ok(ChatChunk::Thinking(rt.to_string())));
                        }
                    }
                    if let Some(tcs) = delta.get("tool_calls").and_then(|t| t.as_array()) {
                        for tc in tcs {
                            let idx = tc.get("index").and_then(|i| i.as_u64()).unwrap_or(0) as u32;
                            let buf = self.tool_calls.entry(idx).or_default();
                            if let Some(id) = tc.get("id").and_then(|v| v.as_str()) {
                                buf.id = id.to_string();
                            }
                            if let Some(fn_obj) = tc.get("function") {
                                if let Some(n) = fn_obj.get("name").and_then(|v| v.as_str()) {
                                    if !n.is_empty() {
                                        buf.name = n.to_string();
                                    }
                                }
                                if let Some(a) = fn_obj.get("arguments").and_then(|v| v.as_str()) {
                                    buf.args.push_str(a);
                                }
                            }
                        }
                    }
                }
                if let Some(fr) = choice.get("finish_reason").and_then(|f| f.as_str()) {
                    self.last_finish = Some(fr.to_string());
                }
            }
        }
        out
    }

    fn finalize(&mut self) -> Vec<Result<ChatChunk, LlmError>> {
        if self.finalized {
            return vec![];
        }
        self.finalized = true;
        let mut out = Vec::new();
        let mut keys: Vec<u32> = self.tool_calls.keys().copied().collect();
        keys.sort_unstable();
        for k in keys {
            if let Some(buf) = self.tool_calls.remove(&k) {
                let input: serde_json::Value = if buf.args.is_empty() {
                    serde_json::Value::Object(Default::default())
                } else {
                    serde_json::from_str(&buf.args)
                        .unwrap_or_else(|_| serde_json::Value::Object(Default::default()))
                };
                let id = if buf.id.is_empty() {
                    format!("call_{k}")
                } else {
                    buf.id
                };
                out.push(Ok(ChatChunk::ToolCall {
                    id,
                    name: buf.name,
                    input,
                }));
            }
        }
        if let Some(u) = self.last_usage.take() {
            out.push(Ok(ChatChunk::Usage(u)));
        }
        out.push(Ok(ChatChunk::Stop {
            reason: self
                .last_finish
                .clone()
                .unwrap_or_else(|| "stop".to_string()),
        }));
        out
    }
}

// -----------------------------------------------------------------------------
// 消息序列化:ChatMessage -> OpenAI chat schema
// -----------------------------------------------------------------------------

fn messages_to_openai(system: &Option<String>, messages: &[ChatMessage]) -> serde_json::Value {
    let mut out = Vec::new();
    if let Some(sys) = system {
        out.push(serde_json::json!({"role": "system", "content": sys}));
    }
    for m in messages {
        match m.role {
            ChatRole::System => {
                // 已在外部追加;若用户在消息里也塞了 system,合并进去。
                if let Some(text) = &m.content {
                    out.push(serde_json::json!({"role": "system", "content": text}));
                }
            }
            ChatRole::User => {
                let content = user_content(m);
                out.push(serde_json::json!({"role": "user", "content": content}));
            }
            ChatRole::Assistant => {
                let mut entry = serde_json::Map::new();
                entry.insert("role".into(), serde_json::json!("assistant"));
                if let Some(text) = &m.content {
                    entry.insert("content".into(), serde_json::json!(text));
                }
                if !m.tool_calls.is_empty() {
                    let tcs: Vec<serde_json::Value> = m
                        .tool_calls
                        .iter()
                        .map(|tc: &ToolCall| {
                            serde_json::json!({
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": serde_json::to_string(&tc.input).unwrap_or_else(|_| "{}".into()),
                                }
                            })
                        })
                        .collect();
                    entry.insert("tool_calls".into(), serde_json::Value::Array(tcs));
                }
                out.push(serde_json::Value::Object(entry));
            }
            ChatRole::Tool => {
                out.push(serde_json::json!({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id.clone().unwrap_or_default(),
                    "content": m.content.clone().unwrap_or_default(),
                }));
            }
        }
    }
    serde_json::Value::Array(out)
}

fn user_content(m: &ChatMessage) -> serde_json::Value {
    if m.parts.is_empty() {
        return serde_json::Value::String(m.content.clone().unwrap_or_default());
    }
    let mut blocks = Vec::new();
    if let Some(text) = &m.content {
        if !text.is_empty() {
            blocks.push(serde_json::json!({"type": "text", "text": text}));
        }
    }
    for p in &m.parts {
        match p {
            MessagePart::Text { text } => {
                blocks.push(serde_json::json!({"type": "text", "text": text}));
            }
            MessagePart::Image { source, .. } => {
                blocks.push(serde_json::json!({
                    "type": "image_url",
                    "image_url": {"url": source},
                }));
            }
            // tool_use / tool_result 在 user 消息里不出现;忽略。
            _ => {}
        }
    }
    if blocks.is_empty() {
        serde_json::Value::String(m.content.clone().unwrap_or_default())
    } else {
        serde_json::Value::Array(blocks)
    }
}

fn parse_openai_usage(v: &serde_json::Value) -> Usage {
    Usage {
        input_tokens: v
            .get("prompt_tokens")
            .and_then(|n| n.as_u64())
            .unwrap_or(0) as u32,
        output_tokens: v
            .get("completion_tokens")
            .and_then(|n| n.as_u64())
            .unwrap_or(0) as u32,
        cache_read: v
            .get("prompt_tokens_details")
            .and_then(|d| d.get("cached_tokens"))
            .and_then(|n| n.as_u64())
            .unwrap_or(0) as u32,
        cache_create: 0,
        reasoning_tokens: v
            .get("completion_tokens_details")
            .and_then(|d| d.get("reasoning_tokens"))
            .and_then(|n| n.as_u64())
            .unwrap_or(0) as u32,
    }
}

fn clip(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        s.chars().take(n).collect::<String>() + "…"
    }
}
