//! responses — OpenAI Responses API (`POST /v1/responses`)。
//!
//! 对应 Python `rpg/agents/gm/backends/openai_compat.py` 里的 Responses 分支。
//! 与 Chat Completions 的区别:
//!   * 顶层字段:`input` (array of items) 替代 `messages`;`instructions` 替代
//!     `system`;`tools` 直接是 function 列表(不再嵌套 type:function)。
//!   * 流响应:每个 SSE 事件带 `event: response.xxx` 类型,载荷为差异 payload。
//!     主要事件:`response.created` / `response.output_text.delta` /
//!     `response.output_text.done` / `response.function_call_arguments.delta` /
//!     `response.completed` / `response.error`。
//!   * Reasoning 模型 (o-系列) 通过 `response.reasoning_summary.delta` 流出
//!     reasoning summary 文本(我们映射成 ChatChunk::Thinking)。
//!
//! 复用 `OpenAiBackend` 的 base_url / api_key / http client,通过 `ResponsesBackend`
//! 暴露 `LlmBackend` trait;`use_responses_api` 字段控制走 Responses 还是 Chat。

use std::collections::HashMap;

use async_trait::async_trait;
use eventsource_stream::Eventsource;
use futures_util::stream::{self, StreamExt, TryStreamExt};

use crate::pipeline::{
    build_http_client, namespaced_tool_name, BackendKind, ChatChunk, ChatMessage, ChatRequest,
    ChatRole, ChunkStream, LlmBackend, LlmError, MessagePart, ModelInfo, Usage,
};

const DEFAULT_BASE_URL: &str = "https://api.openai.com/v1";

/// OpenAI Responses API backend。
pub struct ResponsesBackend {
    api_key: String,
    base_url: String,
    http: reqwest::Client,
    pub api_id: String,
}

impl ResponsesBackend {
    pub fn new(api_key: impl Into<String>) -> Result<Self, LlmError> {
        Self::new_with(api_key, DEFAULT_BASE_URL, "openai_responses")
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
        if let Some(sys) = &req.system {
            body.insert("instructions".into(), serde_json::Value::String(sys.clone()));
        }
        body.insert("input".into(), messages_to_responses_input(&req.messages));
        if let Some(m) = req.max_tokens {
            body.insert("max_output_tokens".into(), serde_json::json!(m));
        }
        if let Some(t) = req.temperature {
            body.insert("temperature".into(), serde_json::json!(t));
        }
        if req.stream {
            body.insert("stream".into(), serde_json::Value::Bool(true));
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
                    // Responses API 工具 schema 顶层就是 {type:"function", name, ...}
                    serde_json::json!({
                        "type": "function",
                        "name": name,
                        "description": t.description,
                        "parameters": params,
                    })
                })
                .collect();
            body.insert("tools".into(), serde_json::Value::Array(tools));
            if let Some(tc) = req.extra.get("tool_choice") {
                body.insert("tool_choice".into(), tc.clone());
            }
        }
        // reasoning effort:o-系列模型 (e.g. o3-mini)。
        if let Some(r) = req.extra.get("reasoning") {
            body.insert("reasoning".into(), r.clone());
        } else if let Some(eff) = req.extra.get("reasoning_effort") {
            body.insert(
                "reasoning".into(),
                serde_json::json!({"effort": eff}),
            );
        }
        // 透传 extra (response_format, top_p, seed, user, parallel_tool_calls, ...)
        if let Some(obj) = req.extra.as_object() {
            for (k, v) in obj {
                if matches!(
                    k.as_str(),
                    "tool_choice" | "headers" | "reasoning" | "reasoning_effort"
                ) {
                    continue;
                }
                body.insert(k.clone(), v.clone());
            }
        }
        Ok(serde_json::Value::Object(body))
    }
}

#[async_trait]
impl LlmBackend for ResponsesBackend {
    fn kind(&self) -> BackendKind {
        // Responses 是 OpenAI 专属端点,即便 base_url 被覆盖,也归类成 OpenAI。
        BackendKind::Openai
    }

    async fn stream_chat<'a>(&'a self, req: ChatRequest) -> Result<ChunkStream<'a>, LlmError> {
        let body = self.build_body(&req)?;
        let url = format!("{}/responses", self.base_url.trim_end_matches('/'));
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
            // 非流模式:直接解析完整 Response 对象。
            let payload: serde_json::Value = resp.json().await?;
            let mut out: Vec<Result<ChatChunk, LlmError>> = Vec::new();
            collect_response_output(&payload, &mut out);
            if let Some(usage) = payload.get("usage") {
                out.push(Ok(ChatChunk::Usage(parse_responses_usage(usage))));
            }
            let reason = payload
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("completed")
                .to_string();
            out.push(Ok(ChatChunk::Stop { reason }));
            return Ok(Box::pin(stream::iter(out)));
        }

        let event_stream = resp
            .bytes_stream()
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))
            .eventsource()
            .map_err(|e| LlmError::Stream(e.to_string()));

        let parsed = event_stream.scan(ResponsesStreamState::default(), |state, ev_res| {
            let chunks = match ev_res {
                Ok(ev) => state.process(&ev.event, &ev.data),
                Err(e) => vec![Err(e)],
            };
            futures_util::future::ready(Some(chunks))
        });
        let flat = parsed.flat_map(stream::iter);
        Ok(Box::pin(flat))
    }

    async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
        // 直接复用 /v1/models 端点。
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
                                    capabilities: vec![
                                        "text".into(),
                                        "streaming".into(),
                                        "tools".into(),
                                        "reasoning".into(),
                                    ],
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
}

// -----------------------------------------------------------------------------
// SSE 流状态机 (Responses API)
// -----------------------------------------------------------------------------

#[derive(Debug, Default)]
struct ResponsesStreamState {
    /// item_id → tool call 累加器。Responses 流通过 item_id + function_call.arguments.delta
    /// 增量推送 tool input。
    tools: HashMap<String, ToolBuf>,
    last_status: Option<String>,
    last_usage: Option<Usage>,
    finalized: bool,
}

#[derive(Debug, Default)]
struct ToolBuf {
    call_id: String,
    name: String,
    args: String,
}

impl ResponsesStreamState {
    fn process(&mut self, event: &str, data: &str) -> Vec<Result<ChatChunk, LlmError>> {
        if data.trim().is_empty() {
            return vec![];
        }
        let value: serde_json::Value = match serde_json::from_str(data) {
            Ok(v) => v,
            Err(e) => {
                return vec![Ok(ChatChunk::Error(format!(
                    "openai responses sse parse: {e}; event={event}; data={}",
                    clip(data, 200)
                )))];
            }
        };
        let mut out = Vec::new();
        match event {
            "response.output_text.delta" => {
                if let Some(d) = value.get("delta").and_then(|v| v.as_str()) {
                    if !d.is_empty() {
                        out.push(Ok(ChatChunk::Text(d.to_string())));
                    }
                }
            }
            "response.reasoning_summary_text.delta"
            | "response.reasoning_summary.delta"
            | "response.reasoning.delta" => {
                if let Some(d) = value.get("delta").and_then(|v| v.as_str()) {
                    if !d.is_empty() {
                        out.push(Ok(ChatChunk::Thinking(d.to_string())));
                    }
                }
            }
            "response.output_item.added" => {
                if let Some(item) = value.get("item") {
                    let item_type = item.get("type").and_then(|v| v.as_str()).unwrap_or("");
                    if item_type == "function_call" {
                        let id = item
                            .get("id")
                            .and_then(|v| v.as_str())
                            .unwrap_or_default()
                            .to_string();
                        let call_id = item
                            .get("call_id")
                            .and_then(|v| v.as_str())
                            .unwrap_or(&id)
                            .to_string();
                        let name = item
                            .get("name")
                            .and_then(|v| v.as_str())
                            .unwrap_or_default()
                            .to_string();
                        self.tools.insert(
                            id,
                            ToolBuf {
                                call_id,
                                name,
                                args: String::new(),
                            },
                        );
                    }
                }
            }
            "response.function_call_arguments.delta" => {
                if let (Some(item_id), Some(delta)) = (
                    value.get("item_id").and_then(|v| v.as_str()),
                    value.get("delta").and_then(|v| v.as_str()),
                ) {
                    if let Some(buf) = self.tools.get_mut(item_id) {
                        buf.args.push_str(delta);
                    } else {
                        // item_added 缺失时的兜底:直接建一个。
                        let mut buf = ToolBuf::default();
                        buf.call_id = item_id.to_string();
                        buf.args.push_str(delta);
                        self.tools.insert(item_id.to_string(), buf);
                    }
                }
            }
            "response.function_call_arguments.done" | "response.output_item.done" => {
                // 拿到 item.id 时把累加的 args 收尾并 emit ToolCall。
                let item_id = value
                    .get("item_id")
                    .and_then(|v| v.as_str())
                    .or_else(|| value.get("item").and_then(|i| i.get("id")).and_then(|v| v.as_str()))
                    .map(|s| s.to_string());
                if let Some(id) = item_id {
                    if let Some(buf) = self.tools.remove(&id) {
                        let input: serde_json::Value = if buf.args.is_empty() {
                            serde_json::Value::Object(Default::default())
                        } else {
                            serde_json::from_str(&buf.args).unwrap_or_else(|_| {
                                serde_json::Value::Object(Default::default())
                            })
                        };
                        let call_id = if buf.call_id.is_empty() { id } else { buf.call_id };
                        out.push(Ok(ChatChunk::ToolCall {
                            id: call_id,
                            name: buf.name,
                            input,
                        }));
                    }
                }
            }
            "response.completed" | "response.incomplete" | "response.failed" => {
                if let Some(resp) = value.get("response") {
                    if let Some(usage) = resp.get("usage") {
                        self.last_usage = Some(parse_responses_usage(usage));
                    }
                    if let Some(st) = resp.get("status").and_then(|v| v.as_str()) {
                        self.last_status = Some(st.to_string());
                    }
                }
                out.extend(self.finalize());
            }
            "response.error" | "error" => {
                let msg = value
                    .get("message")
                    .or_else(|| value.get("error").and_then(|e| e.get("message")))
                    .and_then(|v| v.as_str())
                    .unwrap_or("openai responses stream error")
                    .to_string();
                out.push(Ok(ChatChunk::Error(msg)));
            }
            _ => { /* 未知事件忽略 (response.created/in_progress 之类) */ }
        }
        out
    }

    fn finalize(&mut self) -> Vec<Result<ChatChunk, LlmError>> {
        if self.finalized {
            return vec![];
        }
        self.finalized = true;
        let mut out = Vec::new();
        // 残留未关闭的 tool buf (理论上 .done 已经处理完;兜底)
        let leftover: Vec<String> = self.tools.keys().cloned().collect();
        for id in leftover {
            if let Some(buf) = self.tools.remove(&id) {
                let input: serde_json::Value = if buf.args.is_empty() {
                    serde_json::Value::Object(Default::default())
                } else {
                    serde_json::from_str(&buf.args)
                        .unwrap_or_else(|_| serde_json::Value::Object(Default::default()))
                };
                let call_id = if buf.call_id.is_empty() { id } else { buf.call_id };
                out.push(Ok(ChatChunk::ToolCall {
                    id: call_id,
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
                .last_status
                .clone()
                .unwrap_or_else(|| "completed".to_string()),
        }));
        out
    }
}

// -----------------------------------------------------------------------------
// 输入序列化:ChatMessage[] -> Responses API input items
// -----------------------------------------------------------------------------

/// Responses API 把所有消息表达成 input items;message 是其中一种 type。
fn messages_to_responses_input(messages: &[ChatMessage]) -> serde_json::Value {
    let mut out = Vec::new();
    for m in messages {
        match m.role {
            ChatRole::System => {
                if let Some(text) = &m.content {
                    out.push(serde_json::json!({
                        "type": "message",
                        "role": "system",
                        "content": [ {"type": "input_text", "text": text} ],
                    }));
                }
            }
            ChatRole::User => {
                let content = user_content_responses(m);
                out.push(serde_json::json!({
                    "type": "message",
                    "role": "user",
                    "content": content,
                }));
            }
            ChatRole::Assistant => {
                // assistant text → output_text message;tool_calls → function_call items。
                if let Some(text) = &m.content {
                    if !text.is_empty() {
                        out.push(serde_json::json!({
                            "type": "message",
                            "role": "assistant",
                            "content": [ {"type": "output_text", "text": text} ],
                        }));
                    }
                }
                for tc in &m.tool_calls {
                    out.push(serde_json::json!({
                        "type": "function_call",
                        "call_id": tc.id,
                        "name": tc.name,
                        "arguments": serde_json::to_string(&tc.input).unwrap_or_else(|_| "{}".into()),
                    }));
                }
            }
            ChatRole::Tool => {
                out.push(serde_json::json!({
                    "type": "function_call_output",
                    "call_id": m.tool_call_id.clone().unwrap_or_default(),
                    "output": m.content.clone().unwrap_or_default(),
                }));
            }
        }
    }
    serde_json::Value::Array(out)
}

fn user_content_responses(m: &ChatMessage) -> serde_json::Value {
    if m.parts.is_empty() {
        return serde_json::json!([
            {"type": "input_text", "text": m.content.clone().unwrap_or_default()}
        ]);
    }
    let mut blocks: Vec<serde_json::Value> = Vec::new();
    if let Some(text) = &m.content {
        if !text.is_empty() {
            blocks.push(serde_json::json!({"type": "input_text", "text": text}));
        }
    }
    for p in &m.parts {
        match p {
            MessagePart::Text { text } => {
                blocks.push(serde_json::json!({"type": "input_text", "text": text}));
            }
            MessagePart::Image { source, .. } => {
                blocks.push(serde_json::json!({
                    "type": "input_image",
                    "image_url": source,
                }));
            }
            MessagePart::FileData { file_uri, .. } => {
                blocks.push(serde_json::json!({
                    "type": "input_file",
                    "file_url": file_uri,
                }));
            }
            MessagePart::InlineData { mime_type, data } => {
                use base64::Engine;
                let encoded = base64::engine::general_purpose::STANDARD.encode(data);
                let data_url = format!("data:{};base64,{}", mime_type, encoded);
                if mime_type.starts_with("image/") {
                    blocks.push(serde_json::json!({
                        "type": "input_image",
                        "image_url": data_url,
                    }));
                } else {
                    blocks.push(serde_json::json!({
                        "type": "input_file",
                        "file_data": data_url,
                    }));
                }
            }
            // tool_use / tool_result 不在 user 消息出现
            _ => {}
        }
    }
    serde_json::Value::Array(blocks)
}

// -----------------------------------------------------------------------------
// Helper:非流 + 流通用的 output 收集
// -----------------------------------------------------------------------------

fn collect_response_output(value: &serde_json::Value, out: &mut Vec<Result<ChatChunk, LlmError>>) {
    let items = value.get("output").and_then(|v| v.as_array());
    let Some(items) = items else { return };
    for item in items {
        let item_type = item.get("type").and_then(|v| v.as_str()).unwrap_or("");
        match item_type {
            "message" => {
                if let Some(content) = item.get("content").and_then(|c| c.as_array()) {
                    for c in content {
                        let ct = c.get("type").and_then(|v| v.as_str()).unwrap_or("");
                        if ct == "output_text" || ct == "text" {
                            if let Some(t) = c.get("text").and_then(|v| v.as_str()) {
                                if !t.is_empty() {
                                    out.push(Ok(ChatChunk::Text(t.to_string())));
                                }
                            }
                        }
                    }
                }
            }
            "reasoning" => {
                if let Some(summary) = item.get("summary").and_then(|s| s.as_array()) {
                    for s in summary {
                        if let Some(t) = s.get("text").and_then(|v| v.as_str()) {
                            if !t.is_empty() {
                                out.push(Ok(ChatChunk::Thinking(t.to_string())));
                            }
                        }
                    }
                }
            }
            "function_call" => {
                let id = item
                    .get("call_id")
                    .and_then(|v| v.as_str())
                    .or_else(|| item.get("id").and_then(|v| v.as_str()))
                    .unwrap_or_default()
                    .to_string();
                let name = item
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_string();
                let args_str = item
                    .get("arguments")
                    .and_then(|v| v.as_str())
                    .unwrap_or("{}");
                let input: serde_json::Value = serde_json::from_str(args_str)
                    .unwrap_or_else(|_| serde_json::Value::Object(Default::default()));
                out.push(Ok(ChatChunk::ToolCall { id, name, input }));
            }
            _ => {}
        }
    }
}

fn parse_responses_usage(v: &serde_json::Value) -> Usage {
    Usage {
        input_tokens: v
            .get("input_tokens")
            .and_then(|n| n.as_u64())
            .unwrap_or(0) as u32,
        output_tokens: v
            .get("output_tokens")
            .and_then(|n| n.as_u64())
            .unwrap_or(0) as u32,
        cache_read: v
            .get("input_tokens_details")
            .and_then(|d| d.get("cached_tokens"))
            .and_then(|n| n.as_u64())
            .unwrap_or(0) as u32,
        cache_create: 0,
        reasoning_tokens: v
            .get("output_tokens_details")
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
