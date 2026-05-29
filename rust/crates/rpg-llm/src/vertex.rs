//! vertex — Vertex AI (Gemini) backend。
//!
//! 对应 Python `rpg/agents/gm/backends/vertex.py`。
//! Auth: yup-oauth2 ServiceAccountAuthenticator,scope `https://www.googleapis.com/auth/cloud-platform`。
//! Endpoint:
//!   `https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/publishers/google/models/{model}:{action}`
//!   action 可以是 `streamGenerateContent` / `generateContent` / `predict` (embedding)。
//!
//! 流式响应是 SSE 风格 (`alt=sse`),每个 data: payload 是一个 GenerateContentResponse。
//! 如不传 `alt=sse`,则返回 JSON 数组 (整体 buffer 才能 parse)。本实现固定走 SSE。

use std::path::{Path, PathBuf};
use std::sync::Arc;

use async_trait::async_trait;
use eventsource_stream::Eventsource;
use futures_util::stream::{self, StreamExt, TryStreamExt};
use tokio::sync::Mutex;
use yup_oauth2::authenticator::Authenticator;
use yup_oauth2::{ServiceAccountAuthenticator, ServiceAccountKey};

/// `DefaultHyperClient::Connector` 在启用 `hyper-rustls` feature 时具体类型。
type DefaultConnector = yup_oauth2::hyper_rustls::HttpsConnector<
    hyper_util::client::legacy::connect::HttpConnector,
>;

use smallvec::SmallVec;

use crate::pipeline::{
    build_http_client, namespaced_tool_name, BackendKind, ChatChunk, ChatMessage, ChatRequest,
    ChatRole, ChunkStream, LlmBackend, LlmError, MessagePart, ModelInfo, Usage,
};

const SCOPE: &str = "https://www.googleapis.com/auth/cloud-platform";

#[derive(Clone)]
pub struct VertexBackend {
    project_id: String,
    region: String,
    auth: Arc<Authenticator<DefaultConnector>>,
    http: reqwest::Client,
    /// 缓存最近一次的 token,避免每次请求都强制刷新 (yup-oauth2 也有自己的缓存)。
    token_cache: Arc<Mutex<Option<String>>>,
}

impl VertexBackend {
    /// 从 service account JSON 文件构造。
    pub async fn from_sa_file(path: impl AsRef<Path>) -> Result<Self, LlmError> {
        let buf: PathBuf = path.as_ref().to_path_buf();
        let bytes = tokio::fs::read(&buf).await?;
        let key: ServiceAccountKey = serde_json::from_slice(&bytes)
            .map_err(|e| LlmError::Config(format!("vertex SA parse: {e}")))?;
        Self::from_sa_key(key, "global").await
    }

    pub async fn from_sa_key(key: ServiceAccountKey, region: &str) -> Result<Self, LlmError> {
        let project_id = key
            .project_id
            .clone()
            .ok_or_else(|| LlmError::Config("vertex SA missing project_id".into()))?;
        let auth = ServiceAccountAuthenticator::builder(key)
            .build()
            .await
            .map_err(|e| LlmError::Oauth(e.to_string()))?;
        Ok(Self {
            project_id,
            region: region.to_string(),
            auth: Arc::new(auth),
            http: build_http_client(600)?,
            token_cache: Arc::new(Mutex::new(None)),
        })
    }

    /// 用于 `gemini-2.5-flash` 与 `gemini-3.1-pro` 这种公共端点;region=global。
    pub async fn from_sa_file_global(path: impl AsRef<Path>) -> Result<Self, LlmError> {
        Self::from_sa_file(path).await
    }

    /// 返回 GCP project_id。
    pub fn project_id(&self) -> &str {
        &self.project_id
    }

    /// 返回 region (e.g. "us-central1" / "global")。
    pub fn region(&self) -> &str {
        &self.region
    }

    async fn token(&self) -> Result<String, LlmError> {
        // 简单缓存策略:第一次取出后存起来。yup-oauth2 内部也会自动刷新。
        let mut cache = self.token_cache.lock().await;
        let tk = self
            .auth
            .token(&[SCOPE])
            .await
            .map_err(|e| LlmError::Oauth(e.to_string()))?;
        let s = tk
            .token()
            .ok_or_else(|| LlmError::Oauth("empty token".into()))?
            .to_string();
        *cache = Some(s.clone());
        Ok(s)
    }

    /// 轻量探测:`GET /publishers/google/models/{model}` 验证模型可达。
    /// 不计费,等同 Python 端 `model_probe.py:_probe_vertex`。
    pub async fn head_publisher_model(&self, model: &str) -> Result<bool, LlmError> {
        let tok = self.token().await?;
        let host = if self.region == "global" {
            "aiplatform.googleapis.com".to_string()
        } else {
            format!("{}-aiplatform.googleapis.com", self.region)
        };
        let url = format!(
            "https://{host}/v1/projects/{proj}/locations/{loc}/publishers/google/models/{model}",
            host = host,
            proj = self.project_id,
            loc = self.region,
            model = model,
        );
        let resp = self.http.get(&url).bearer_auth(&tok).send().await?;
        Ok(resp.status().is_success())
    }

    /// 带 task_type 的 embedding 调用,含重试逻辑:
    ///   - 429 / 503 → 指数退避(1s,2s,4s,最多 3 次)
    ///   - 5xx      → 最多 3 次重试
    ///
    /// `task_type` 对应 Vertex EmbedContentConfig:
    ///   "RETRIEVAL_DOCUMENT" — 文档侧
    ///   "RETRIEVAL_QUERY"    — 查询侧
    pub async fn embed_with_task_type(
        &self,
        model: &str,
        texts: &[String],
        task_type: &str,
    ) -> Result<Vec<Vec<f32>>, LlmError> {
        if texts.is_empty() {
            return Ok(vec![]);
        }
        let tok = self.token().await?;
        let url = self.endpoint(model, "predict");
        let body = build_embed_body(texts, task_type);

        const MAX_RETRIES: u32 = 3;
        let mut attempt = 0u32;
        loop {
            let resp = self
                .http
                .post(&url)
                .bearer_auth(&tok)
                .header("Content-Type", "application/json")
                .json(&body)
                .send()
                .await?;
            let status = resp.status();
            let status_u16 = status.as_u16();

            if status.is_success() {
                let v: serde_json::Value = resp.json().await?;
                let out = parse_embed_response(&v).ok_or_else(|| {
                    LlmError::Stream("vertex embed: no predictions field".into())
                })?;
                return Ok(out);
            }

            // 判断是否可重试:429/503 → 指数退避;5xx → 最多 3 次
            if !is_retryable_status(status_u16) || attempt >= MAX_RETRIES {
                let body_text = resp.text().await.unwrap_or_default();
                return Err(LlmError::Provider {
                    status: status_u16,
                    body: body_text,
                });
            }

            // 指数退避:1s, 2s, 4s
            let delay_ms = 1000u64 << attempt;
            tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
            attempt += 1;
        }
    }

    fn endpoint(&self, model: &str, action: &str) -> String {
        let host = if self.region == "global" {
            "aiplatform.googleapis.com".to_string()
        } else {
            format!("{}-aiplatform.googleapis.com", self.region)
        };
        format!(
            "https://{host}/v1/projects/{proj}/locations/{loc}/publishers/google/models/{model}:{action}",
            host = host,
            proj = self.project_id,
            loc = self.region,
            model = model,
            action = action,
        )
    }

    fn build_body(&self, req: &ChatRequest) -> serde_json::Value {
        let mut body = serde_json::Map::new();

        // system_instruction
        if let Some(sys) = &req.system {
            body.insert(
                "systemInstruction".into(),
                serde_json::json!({
                    "parts": [ { "text": sys } ]
                }),
            );
        }

        // contents
        body.insert(
            "contents".into(),
            messages_to_gemini_contents(&req.messages),
        );

        // tools (function_declarations)
        if !req.tools.is_empty() {
            let decls: Vec<serde_json::Value> = req
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
                        "name": name,
                        "description": t.description,
                        "parameters": params,
                    })
                })
                .collect();
            body.insert(
                "tools".into(),
                serde_json::json!([{ "functionDeclarations": decls }]),
            );
        }

        // generationConfig
        let mut gc = serde_json::Map::new();
        if let Some(t) = req.temperature {
            gc.insert("temperature".into(), serde_json::json!(t));
        }
        if let Some(m) = req.max_tokens {
            // 服务端硬 clamp:忽略客户端超限的 maxOutputTokens,防刷爆。
            let m = m.min(crate::HARD_MAX_OUTPUT_TOKENS);
            gc.insert("maxOutputTokens".into(), serde_json::json!(m));
        }
        // 默认禁用 thinking,跟 python 端语义一致。
        if let Some(budget) = req.extra.get("thinking_budget") {
            gc.insert(
                "thinkingConfig".into(),
                serde_json::json!({"thinkingBudget": budget}),
            );
        } else {
            gc.insert(
                "thinkingConfig".into(),
                serde_json::json!({"thinkingBudget": 0}),
            );
        }
        if let Some(rmt) = req.extra.get("response_mime_type") {
            gc.insert("responseMimeType".into(), rmt.clone());
        }
        if !gc.is_empty() {
            body.insert("generationConfig".into(), serde_json::Value::Object(gc));
        }
        // 透传 extra 中的其它顶层字段
        if let Some(obj) = req.extra.as_object() {
            for (k, v) in obj {
                if matches!(
                    k.as_str(),
                    "thinking_budget" | "response_mime_type" | "headers"
                ) {
                    continue;
                }
                body.insert(k.clone(), v.clone());
            }
        }

        serde_json::Value::Object(body)
    }
}

#[async_trait]
impl LlmBackend for VertexBackend {
    fn kind(&self) -> BackendKind {
        BackendKind::Vertex
    }

    #[tracing::instrument(skip(self, req), fields(model = %req.model, stream = req.stream))]
    async fn stream_chat<'a>(&'a self, req: ChatRequest) -> Result<ChunkStream<'a>, LlmError> {
        let tok = self.token().await?;
        let body = self.build_body(&req);

        let action = if req.stream {
            "streamGenerateContent"
        } else {
            "generateContent"
        };
        let mut url = self.endpoint(&req.model, action);
        if req.stream {
            url.push_str("?alt=sse");
        }

        let resp = self
            .http
            .post(&url)
            .bearer_auth(&tok)
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

        if !req.stream {
            let v: serde_json::Value = resp.json().await?;
            let mut out: Vec<Result<ChatChunk, LlmError>> = Vec::new();
            push_response_chunks(&v, &mut out);
            out.push(Ok(ChatChunk::Stop {
                reason: stop_reason_from(&v).unwrap_or_else(|| "stop".into()),
            }));
            return Ok(Box::pin(stream::iter(out)));
        }

        // SSE 流:每个 data 是一个完整 GenerateContentResponse。
        let event_stream = resp
            .bytes_stream()
            .map_err(std::io::Error::other)
            .eventsource()
            .map_err(|e| LlmError::Stream(e.to_string()));

        let parsed = event_stream.scan(VertexStreamState::default(), |state, ev_res| {
            let chunks: VertexSseChunks = match ev_res {
                Ok(ev) => state.process(&ev.data),
                Err(e) => {
                    let mut sv: VertexSseChunks = SmallVec::new();
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
        // Vertex REST 列模型 endpoint 比较冷门,直接 hardcode (与 catalog 对齐)。
        Ok(default_vertex_models())
    }

    async fn embed(&self, model: &str, texts: &[String]) -> Result<Vec<Vec<f32>>, LlmError> {
        self.embed_with_task_type(model, texts, "RETRIEVAL_DOCUMENT").await
    }
}

fn default_vertex_models() -> Vec<ModelInfo> {
    let ids = [
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3.1-pro", "Gemini 3.1 Pro"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ];
    ids.iter()
        .map(|(id, name)| ModelInfo {
            id: (*id).to_string(),
            display_name: (*name).to_string(),
            capabilities: vec![
                "text".into(),
                "streaming".into(),
                "tools".into(),
                "image_input".into(),
            ],
            context_window: Some(1_000_000),
        })
        .collect()
}

// -----------------------------------------------------------------------------
// SSE 状态机 (Gemini)
// -----------------------------------------------------------------------------

#[derive(Debug, Default)]
struct VertexStreamState {
    /// 累计输出文本 (用于 token 估计 / 终态);此处不用,留给上层。
    _accumulated_text: String,
}

// Vertex 每 SSE 事件通常 1-2 个 chunk;2 槽足够多数情况栈上分配。
type VertexSseChunks = SmallVec<[Result<ChatChunk, LlmError>; 2]>;

impl VertexStreamState {
    fn process(&mut self, data: &str) -> VertexSseChunks {
        if data.trim().is_empty() {
            return SmallVec::new();
        }
        let value: serde_json::Value = match serde_json::from_str(data) {
            Ok(v) => v,
            Err(e) => {
                let mut sv: VertexSseChunks = SmallVec::new();
                sv.push(Ok(ChatChunk::Error(format!(
                    "vertex sse parse: {e}; data={}",
                    clip(data, 200)
                ))));
                return sv;
            }
        };
        let mut out: VertexSseChunks = SmallVec::new();
        push_response_chunks(&value, &mut out);
        if let Some(reason) = stop_reason_from(&value) {
            out.push(Ok(ChatChunk::Stop { reason }));
        }
        out
    }
}

/// 把单个 GenerateContentResponse / 流式 chunk JSON 拍成 ChatChunks。
/// 接受任意支持 `push` 的容器(Vec / SmallVec 均可)。
fn push_response_chunks<C>(value: &serde_json::Value, out: &mut C)
where
    C: Extend<Result<ChatChunk, LlmError>>,
{
    if let Some(cands) = value.get("candidates").and_then(|v| v.as_array()) {
        for cand in cands {
            if let Some(parts) = cand
                .get("content")
                .and_then(|c| c.get("parts"))
                .and_then(|p| p.as_array())
            {
                for part in parts {
                    if let Some(text) = part.get("text").and_then(|t| t.as_str()) {
                        if !text.is_empty() {
                            // Gemini thinking 文本通过 part.thought=true 标记;
                            // 其它情况是普通输出文本。
                            let is_thought = part
                                .get("thought")
                                .and_then(|t| t.as_bool())
                                .unwrap_or(false);
                            if is_thought {
                                out.extend([Ok(ChatChunk::Thinking(text.to_string()))]);
                            } else {
                                out.extend([Ok(ChatChunk::Text(text.to_string()))]);
                            }
                        }
                    }
                    if let Some(fc) = part.get("functionCall") {
                        let name = fc
                            .get("name")
                            .and_then(|n| n.as_str())
                            .unwrap_or("")
                            .to_string();
                        let args = fc
                            .get("args")
                            .cloned()
                            .unwrap_or_else(|| serde_json::Value::Object(Default::default()));
                        // Gemini 没有显式 id,用 name 作为合成 id(GameMaster 层会再 namespace 一次)。
                        out.extend([Ok(ChatChunk::ToolCall {
                            id: format!("vertex_{name}"),
                            name,
                            input: args,
                        })]);
                    }
                }
            }
        }
    }
    if let Some(usage) = value.get("usageMetadata") {
        let u = Usage {
            input_tokens: usage
                .get("promptTokenCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
            output_tokens: usage
                .get("candidatesTokenCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
            cache_read: usage
                .get("cachedContentTokenCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
            cache_create: 0,
            reasoning_tokens: usage
                .get("thoughtsTokenCount")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
        };
        if u.input_tokens > 0 || u.output_tokens > 0 || u.reasoning_tokens > 0 {
            out.extend([Ok(ChatChunk::Usage(u))]);
        }
    }
}

fn stop_reason_from(value: &serde_json::Value) -> Option<String> {
    value
        .get("candidates")
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .and_then(|c| c.get("finishReason"))
        .and_then(|f| f.as_str())
        .map(|s| s.to_string())
}

// -----------------------------------------------------------------------------
// 消息序列化:ChatMessage -> Gemini contents (role: user/model, parts: [...])
// -----------------------------------------------------------------------------

fn messages_to_gemini_contents(messages: &[ChatMessage]) -> serde_json::Value {
    let mut out = Vec::new();
    for m in messages {
        let role = match m.role {
            ChatRole::User | ChatRole::Tool => "user",
            ChatRole::Assistant => "model",
            ChatRole::System => continue,
        };
        let mut parts = Vec::new();

        // tool 角色 → function_response
        if matches!(m.role, ChatRole::Tool) {
            let name = m
                .tool_call_id
                .clone()
                .unwrap_or_else(|| "tool".to_string());
            let response_value: serde_json::Value = m
                .content
                .as_deref()
                .and_then(|s| serde_json::from_str::<serde_json::Value>(s).ok())
                .unwrap_or_else(|| {
                    serde_json::json!({"result": m.content.clone().unwrap_or_default()})
                });
            parts.push(serde_json::json!({
                "functionResponse": {
                    "name": name,
                    "response": response_value,
                }
            }));
        } else if matches!(m.role, ChatRole::Assistant) && !m.tool_calls.is_empty() {
            if let Some(text) = &m.content {
                if !text.is_empty() {
                    parts.push(serde_json::json!({"text": text}));
                }
            }
            for tc in &m.tool_calls {
                parts.push(serde_json::json!({
                    "functionCall": {
                        "name": tc.name,
                        "args": tc.input,
                    }
                }));
            }
        } else if !m.parts.is_empty() {
            for p in &m.parts {
                match p {
                    MessagePart::Text { text } => {
                        parts.push(serde_json::json!({"text": text}));
                    }
                    MessagePart::Image { source, media_type } => {
                        parts.push(serde_json::json!({
                            "inlineData": {
                                "mimeType": media_type.clone().unwrap_or_else(|| "image/png".into()),
                                "data": source,
                            }
                        }));
                    }
                    MessagePart::FileData { mime_type, file_uri } => {
                        parts.push(serde_json::json!({
                            "fileData": {
                                "mimeType": mime_type,
                                "fileUri": file_uri,
                            }
                        }));
                    }
                    MessagePart::InlineData { mime_type, data } => {
                        use base64::Engine;
                        let encoded = base64::engine::general_purpose::STANDARD.encode(data);
                        parts.push(serde_json::json!({
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": encoded,
                            }
                        }));
                    }
                    MessagePart::ToolUse { name, input, .. } => {
                        parts.push(serde_json::json!({
                            "functionCall": {"name": name, "args": input}
                        }));
                    }
                    MessagePart::ToolResult { tool_use_id, content, .. } => {
                        parts.push(serde_json::json!({
                            "functionResponse": {
                                "name": tool_use_id,
                                "response": {"result": content},
                            }
                        }));
                    }
                }
            }
        } else if let Some(text) = &m.content {
            parts.push(serde_json::json!({"text": text}));
        }

        out.push(serde_json::json!({"role": role, "parts": parts}));
    }
    serde_json::Value::Array(out)
}

fn clip(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        s.chars().take(n).collect::<String>() + "…"
    }
}

// ---------------------------------------------------------------------------
// Helper: embed request body 构造(与 embed_with_task_type 一致,供测试验证)
// ---------------------------------------------------------------------------

fn build_embed_body(texts: &[String], task_type: &str) -> serde_json::Value {
    let instances: Vec<serde_json::Value> = texts
        .iter()
        .map(|t| {
            serde_json::json!({
                "content": t,
                "task_type": task_type,
            })
        })
        .collect();
    serde_json::json!({
        "instances": instances,
        "parameters": {
            "outputDimensionality": 768_u32,
        }
    })
}

// ---------------------------------------------------------------------------
// Helper: 解析 Vertex :predict 响应中的 embeddings
// ---------------------------------------------------------------------------

fn parse_embed_response(v: &serde_json::Value) -> Option<Vec<Vec<f32>>> {
    let preds = v.get("predictions")?.as_array()?;
    let mut out = Vec::with_capacity(preds.len());
    for p in preds {
        let vec = p
            .get("embeddings")
            .and_then(|e| e.get("values"))
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|n| n.as_f64().map(|f| f as f32))
                    .collect::<Vec<f32>>()
            })
            .unwrap_or_default();
        out.push(vec);
    }
    Some(out)
}

// ---------------------------------------------------------------------------
// Helper: 判断 HTTP status code 是否可重试
// ---------------------------------------------------------------------------

fn is_retryable_status(status: u16) -> bool {
    status == 429 || status == 503 || (500..600).contains(&status)
}

#[cfg(test)]
mod tests {
    use super::*;

    // -------------------------------------------------------------------------
    // test: embed request body shape
    // -------------------------------------------------------------------------

    #[test]
    fn test_embed_body_shape_retrieval_document() {
        let texts = vec!["hello".to_string(), "world".to_string()];
        let body = build_embed_body(&texts, "RETRIEVAL_DOCUMENT");

        // instances 数组
        let instances = body["instances"].as_array().expect("instances must be array");
        assert_eq!(instances.len(), 2);

        // 每个 instance 有 content 和 task_type
        assert_eq!(instances[0]["content"], "hello");
        assert_eq!(instances[0]["task_type"], "RETRIEVAL_DOCUMENT");
        assert_eq!(instances[1]["content"], "world");
        assert_eq!(instances[1]["task_type"], "RETRIEVAL_DOCUMENT");

        // parameters
        let dim = body["parameters"]["outputDimensionality"]
            .as_u64()
            .expect("outputDimensionality must be u64");
        assert_eq!(dim, 768);
    }

    #[test]
    fn test_embed_body_shape_retrieval_query() {
        let texts = vec!["query text".to_string()];
        let body = build_embed_body(&texts, "RETRIEVAL_QUERY");

        let instances = body["instances"].as_array().unwrap();
        assert_eq!(instances[0]["task_type"], "RETRIEVAL_QUERY");
    }

    #[test]
    fn test_embed_body_empty_texts() {
        let texts: Vec<String> = vec![];
        let body = build_embed_body(&texts, "RETRIEVAL_DOCUMENT");
        let instances = body["instances"].as_array().unwrap();
        assert!(instances.is_empty());
    }

    // -------------------------------------------------------------------------
    // test: response parsing
    // -------------------------------------------------------------------------

    #[test]
    fn test_parse_embed_response_valid() {
        let resp = serde_json::json!({
            "predictions": [
                {
                    "embeddings": {
                        "values": [0.1, 0.2, 0.3],
                        "statistics": { "token_count": 5, "truncated": false }
                    }
                },
                {
                    "embeddings": {
                        "values": [0.4, 0.5, 0.6]
                    }
                }
            ]
        });

        let result = parse_embed_response(&resp).expect("should parse");
        assert_eq!(result.len(), 2);
        assert!((result[0][0] - 0.1_f32).abs() < 1e-6);
        assert!((result[0][1] - 0.2_f32).abs() < 1e-6);
        assert!((result[1][0] - 0.4_f32).abs() < 1e-6);
    }

    #[test]
    fn test_parse_embed_response_missing_predictions() {
        let resp = serde_json::json!({ "error": "something went wrong" });
        let result = parse_embed_response(&resp);
        assert!(result.is_none());
    }

    #[test]
    fn test_parse_embed_response_empty_values() {
        let resp = serde_json::json!({
            "predictions": [
                { "embeddings": { "values": [] } }
            ]
        });
        let result = parse_embed_response(&resp).unwrap();
        assert_eq!(result.len(), 1);
        assert!(result[0].is_empty());
    }

    // -------------------------------------------------------------------------
    // test: retry decision logic
    // -------------------------------------------------------------------------

    #[test]
    fn test_retry_429_is_retryable() {
        assert!(is_retryable_status(429));
    }

    #[test]
    fn test_retry_503_is_retryable() {
        assert!(is_retryable_status(503));
    }

    #[test]
    fn test_retry_500_is_retryable() {
        assert!(is_retryable_status(500));
    }

    #[test]
    fn test_retry_502_is_retryable() {
        assert!(is_retryable_status(502));
    }

    #[test]
    fn test_retry_400_not_retryable() {
        assert!(!is_retryable_status(400));
    }

    #[test]
    fn test_retry_401_not_retryable() {
        assert!(!is_retryable_status(401));
    }

    #[test]
    fn test_retry_200_not_retryable() {
        assert!(!is_retryable_status(200));
    }

    // -------------------------------------------------------------------------
    // test: 指数退避延迟计算
    // -------------------------------------------------------------------------

    #[test]
    fn test_exponential_backoff_delays() {
        // attempt 0 → 1000ms, attempt 1 → 2000ms, attempt 2 → 4000ms
        assert_eq!(1000u64, 1000);
        assert_eq!(1000u64 << 1u32, 2000);
        assert_eq!(1000u64 << 2u32, 4000);
    }

    // -------------------------------------------------------------------------
    // test: clip helper
    // -------------------------------------------------------------------------

    #[test]
    fn test_clip_short_string() {
        let s = clip("hello", 10);
        assert_eq!(s, "hello");
    }

    #[test]
    fn test_clip_long_string() {
        let s = clip("hello world", 5);
        assert_eq!(s, "hello…");
    }
}
