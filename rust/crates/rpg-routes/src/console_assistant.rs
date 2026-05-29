//! console_assistant.py → console_assistant.rs — 侧栏控制台助手路由
//! GET  /api/console_assistant/ping                 — 探活
//! GET  /api/console_assistant/conversations        — 列出对话
//! POST /api/console_assistant/new_conversation     — 新建对话
//! POST /api/console_assistant/delete_conversation  — 删除对话
//! POST /api/console_assistant/chat                 — 主聊天 SSE
//! POST /api/console_assistant/confirm              — 确认/拒绝 destructive 工具调用 SSE
//!
//! 翻译期实现:对话用 AppState.console_conversations(全内存)管理,
//! 主 chat / confirm 链路返回简单 SSE stub(等接 LlmRouter 后 wire 真实流式回复)。

use std::convert::Infallible;

use axum::{
    extract::State,
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::{get, post},
    Json, Router,
};
use futures_util::stream::{self, Stream};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::{require_user, AppState, ConsoleMessage, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/console_assistant/ping", get(api_console_assistant_ping))
        .route(
            "/api/console_assistant/conversations",
            get(api_console_assistant_conversations),
        )
        .route(
            "/api/console_assistant/new_conversation",
            post(api_console_assistant_new_conversation),
        )
        .route(
            "/api/console_assistant/delete_conversation",
            post(api_console_assistant_delete_conversation),
        )
        .route("/api/console_assistant/chat", post(api_console_assistant_chat))
        .route(
            "/api/console_assistant/confirm",
            post(api_console_assistant_confirm),
        )
}

// ── request types ─────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct ConsoleAssistantDeleteConversationRequest {
    pub conversation_id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ConsoleAssistantChatRequest {
    pub message: Option<String>,
    pub conversation_id: Option<String>,
    pub page_context: Option<Value>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ConsoleAssistantConfirmRequest {
    pub conversation_id: Option<String>,
    pub call_id: Option<String>,
    /// "approve" | "reject"
    pub decision: Option<String>,
    pub page_context: Option<Value>,
}

fn conv_key(user_id: i64, conv_id: &str) -> String {
    format!("{user_id}:{conv_id}")
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// GET /api/console_assistant/ping
async fn api_console_assistant_ping() -> impl IntoResponse {
    Json(json!({"ok": true, "service": "console_assistant", "version": "1"}))
}

/// GET /api/console_assistant/conversations
async fn api_console_assistant_conversations(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let prefix = format!("{}:", user.id);
    let items: Vec<Value> = s
        .console_conversations
        .iter()
        .filter(|e| e.key().starts_with(&prefix))
        .map(|e| {
            let conv_id = e.key().trim_start_matches(&prefix).to_string();
            json!({
                "conversation_id": conv_id,
                "message_count": e.value().len(),
                "updated_at": e.value().last().map(|m| m.at.to_rfc3339()).unwrap_or_default(),
            })
        })
        .collect();
    Ok(Json(json!({"items": items})).into_response())
}

/// POST /api/console_assistant/new_conversation
async fn api_console_assistant_new_conversation(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let conv_id = format!("conv-{}", uuid::Uuid::new_v4());
    s.console_conversations
        .insert(conv_key(user.id, &conv_id), Vec::new());
    Ok(Json(json!({"ok": true, "conversation_id": conv_id})).into_response())
}

/// POST /api/console_assistant/delete_conversation
async fn api_console_assistant_delete_conversation(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ConsoleAssistantDeleteConversationRequest>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let conv_id = body
        .conversation_id
        .ok_or_else(|| ResponseError::bad_request("conversation_id required"))?;
    s.console_conversations.remove(&conv_key(user.id, &conv_id));
    Ok(Json(json!({"ok": true})).into_response())
}

/// POST /api/console_assistant/chat — SSE(简版)
///
/// 翻译期:把 user message 追加到内存对话,echo 一个空 token + done。
/// 等接 LlmRouter 之后,这里替换为 stream_chat 透传。
async fn api_console_assistant_chat(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ConsoleAssistantChatRequest>,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let conv_id = body
        .conversation_id
        .clone()
        .unwrap_or_else(|| "default".into());
    let message = body.message.unwrap_or_default();
    let key = conv_key(user.id, &conv_id);
    s.console_conversations
        .entry(key)
        .or_insert_with(Vec::new)
        .push(ConsoleMessage {
            role: "user".into(),
            text: message,
            at: chrono::Utc::now(),
        });
    let events = vec![
        Ok::<_, Infallible>(
            Event::default()
                .event("meta")
                .data(json!({"conversation_id": conv_id}).to_string()),
        ),
        Ok(Event::default()
            .event("token")
            .data(json!({"text": ""}).to_string())),
        Ok(Event::default()
            .event("done")
            .data(json!({"ok": true}).to_string())),
    ];
    Ok(Sse::new(stream::iter(events)).keep_alive(KeepAlive::default()))
}

/// POST /api/console_assistant/confirm — SSE
async fn api_console_assistant_confirm(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ConsoleAssistantConfirmRequest>,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    let _user = require_user(&s, &headers).await?;
    let call_id = body.call_id.unwrap_or_default();
    let decision = body.decision.unwrap_or_default();
    let events = vec![
        Ok::<_, Infallible>(
            Event::default()
                .event("tool_result")
                .data(json!({"call_id": call_id, "decision": decision}).to_string()),
        ),
        Ok(Event::default()
            .event("done")
            .data(json!({"ok": true}).to_string())),
    ];
    Ok(Sse::new(stream::iter(events)).keep_alive(KeepAlive::default()))
}
