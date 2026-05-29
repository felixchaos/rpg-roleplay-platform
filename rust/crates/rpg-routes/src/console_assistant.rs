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
use futures_util::stream::StreamExt;
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio_stream::wrappers::ReceiverStream;

use rpg_llm::pipeline::{ChatChunk, ChatMessage, ChatRequest, WireChatChunk};

use crate::sse_metrics::{GuardedStream, SseConnectionGuard};
use crate::{hello_payload, named_sse_event, require_user, AppState, ConsoleMessage, ResponseError};

type SseResponse = Result<Sse<GuardedStream<ReceiverStream<Result<Event, Infallible>>>>, ResponseError>;

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

/// 非 SSE 路由(供 build_regular_routes 使用,排除 /chat 和 /confirm)。
pub fn regular_router() -> Router<AppState> {
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

fn conv_key(user_id: rpg_core::UserId, conv_id: &str) -> String {
    format!("{user_id}:{conv_id}")
}

// ── handlers ──────────────────────────────────────────────────────────────────

/// GET /api/console_assistant/ping
async fn api_console_assistant_ping() -> impl IntoResponse {
    Json(json!({"ok": true, "service": "console_assistant", "version": "1"}))
}

/// GET /api/console_assistant/conversations
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_console_assistant_conversations(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
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
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_console_assistant_new_conversation(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let conv_id = format!("conv-{}", uuid::Uuid::new_v4());
    s.console_conversations
        .insert(conv_key(user.id, &conv_id), Vec::new());
    Ok(Json(json!({"ok": true, "conversation_id": conv_id})).into_response())
}

/// POST /api/console_assistant/delete_conversation
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_console_assistant_delete_conversation(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ConsoleAssistantDeleteConversationRequest>,
) -> Result<Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let conv_id = body
        .conversation_id
        .ok_or_else(|| ResponseError::bad_request("conversation_id required"))?;
    s.console_conversations.remove(&conv_key(user.id, &conv_id));
    Ok(Json(json!({"ok": true})).into_response())
}

/// POST /api/console_assistant/chat — SSE(Wave 6-A:真接 LLM stream)
///
/// 流程:
///   1. 鉴权 + 解析 conversation_id(默认 "default")。
///   2. user message → 追加进 `console_conversations[user:conv]`。
///   3. 取 `llm_router.current_backend()`;无 backend → stub fallback。
///   4. 把会话历史(简化版,只取 role+text)拍成 `ChatMessage` → stream_chat。
///   5. 逐 chunk 转 SSE,assistant 完整文本追加回 conversation。
///   6. 结尾 emit done。
///
/// 不包含:MCP tool 循环 / confirmation_required / page_context 上下文注入。
/// Wave 6-B 起逐步引入(rpg-agents 那边把 MCP loop 抽象起来后)。
#[tracing::instrument(skip(s, headers, body), fields(user_id, conv_id))]
pub(crate) async fn api_console_assistant_chat(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ConsoleAssistantChatRequest>,
) -> SseResponse {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let conv_id = body
        .conversation_id
        .clone()
        .unwrap_or_else(|| "default".into());
    tracing::Span::current().record("conv_id", tracing::field::display(&conv_id));
    let message = body.message.unwrap_or_default();
    let key = conv_key(user.id, &conv_id);
    s.console_conversations
        .entry(key.clone())
        .or_insert_with(Vec::new)
        .push(ConsoleMessage {
            role: "user".into(),
            text: message.clone(),
            at: chrono::Utc::now(),
        });
    let user_id_str = user.id.to_string();

    // SSE 活跃连接 gauge +1。
    let guard = SseConnectionGuard::new("console");

    // backend + model id。
    let backend_opt = s.llm_router.read().current_backend().ok();
    let model_id = s
        .llm_router
        .read()
        .catalog()
        .map(|c| c.selected.model_id.clone())
        .unwrap_or_default();

    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(32);
    let _ = tx
        .send(Ok(named_sse_event("hello", hello_payload(&user_id_str))))
        .await;
    let _ = tx
        .send(Ok(named_sse_event(
            "state_change",
            json!({"conversation_id": conv_id}),
        )))
        .await;

    let Some(backend) = backend_opt else {
        // 无 backend → stub fallback。
        let _ = tx
            .send(Ok(named_sse_event("chunk", json!({"text":""}))))
            .await;
        let _ = tx
            .send(Ok(named_sse_event("done", json!({"ok": true}))))
            .await;
        drop(tx);
        return Ok(Sse::new(GuardedStream::new(ReceiverStream::new(rx), guard)).keep_alive(KeepAlive::default()));
    };

    // 从 conversation 历史构造 ChatMessage。
    let mut messages: Vec<ChatMessage> = Vec::new();
    if let Some(conv) = s.console_conversations.get(&key) {
        for m in conv.iter() {
            match m.role.as_str() {
                "user" => messages.push(ChatMessage::user(m.text.clone())),
                "assistant" => messages.push(ChatMessage::assistant(m.text.clone())),
                _ => {}
            }
        }
    }
    if messages.is_empty() {
        messages.push(ChatMessage::user(message.clone()));
    }

    let mut req = ChatRequest {
        model: model_id,
        system: Some(CONSOLE_ASSISTANT_SYSTEM.to_string()),
        messages,
        max_tokens: Some(CONSOLE_ASSISTANT_MAX_TOKENS),
        stream: true,
        ..Default::default()
    };
    // Wave 10-A:console_assistant 默认 0,运营可通过 RPG_CONSOLE_THINKING_BUDGET 开启。
    rpg_llm::merge_thinking_extra(&mut req.extra, rpg_core::config::console_thinking_budget());

    let conv_key_for_task = key.clone();
    let s_for_task = s.clone();
    tokio::spawn(async move {
        let mut full = String::new();
        match backend.stream_chat(req).await {
            Ok(mut stream) => {
                while let Some(item) = stream.next().await {
                    match item {
                        Ok(chunk) => {
                            if let ChatChunk::Text(t) = &chunk {
                                full.push_str(t);
                            }
                            let wire = WireChatChunk::from_chunk(&chunk);
                            let payload =
                                serde_json::to_value(&wire).unwrap_or_else(|_| json!({}));
                            if tx
                                .send(Ok(named_sse_event("chunk", payload)))
                                .await
                                .is_err()
                            {
                                return;
                            }
                        }
                        Err(e) => {
                            let _ = tx
                                .send(Ok(named_sse_event(
                                    "error",
                                    json!({"detail": e.to_string(), "code": "llm_error"}),
                                )))
                                .await;
                            return;
                        }
                    }
                }
            }
            Err(e) => {
                let _ = tx
                    .send(Ok(named_sse_event(
                        "error",
                        json!({"detail": e.to_string(), "code": "llm_error"}),
                    )))
                    .await;
                return;
            }
        }
        if !full.is_empty() {
            // append assistant reply 到 conversation。
            s_for_task
                .console_conversations
                .entry(conv_key_for_task)
                .or_insert_with(Vec::new)
                .push(ConsoleMessage {
                    role: "assistant".into(),
                    text: full.clone(),
                    at: chrono::Utc::now(),
                });
        }
        let _ = tx
            .send(Ok(named_sse_event("done", json!({"ok": true}))))
            .await;
    });

    Ok(Sse::new(GuardedStream::new(ReceiverStream::new(rx), guard)).keep_alive(KeepAlive::default()))
}

/// console_assistant 用的 system prompt(简化版)。
const CONSOLE_ASSISTANT_SYSTEM: &str = "你是 RPG 控制台侧栏助手,帮玩家操作存档、查阅设定、解释规则。回答简明,优先用要点。";

/// console_assistant max_tokens。
const CONSOLE_ASSISTANT_MAX_TOKENS: u32 = 600;

/// POST /api/console_assistant/confirm — SSE(Wave 6-A:真接 LLM 续写)
///
/// 用户 approve / reject 一个 pending 工具调用后,把决策塞回 conversation
/// 当作 user 视角的"决策声明",触发一次 LLM 续写。无 conversation_id 或
/// 无 backend 时退化老 stub(state_change + done)。
///
/// 不包含:dispatcher 实际调度 destructive tool、navigation_required 事件。
/// 这两块需要等 rpg-tools-dsl 真接 dispatcher,归 Wave 6-B。
#[tracing::instrument(skip(s, headers, body), fields(user_id, call_id))]
pub(crate) async fn api_console_assistant_confirm(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ConsoleAssistantConfirmRequest>,
) -> SseResponse {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let call_id = body.call_id.unwrap_or_default();
    tracing::Span::current().record("call_id", tracing::field::display(&call_id));
    let decision = body.decision.unwrap_or_default();
    let conv_id_opt = body.conversation_id.clone();
    let user_id_str = user.id.to_string();

    // SSE 活跃连接 gauge +1。
    let guard = SseConnectionGuard::new("console");

    let backend_opt = s.llm_router.read().current_backend().ok();
    let model_id = s
        .llm_router
        .read()
        .catalog()
        .map(|c| c.selected.model_id.clone())
        .unwrap_or_default();

    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(32);
    let _ = tx
        .send(Ok(named_sse_event("hello", hello_payload(&user_id_str))))
        .await;
    let _ = tx
        .send(Ok(named_sse_event(
            "state_change",
            json!({"call_id": call_id, "decision": decision}),
        )))
        .await;

    // 无 backend 或无 conversation_id → stub 退化(保持兼容前端 polling 逻辑)。
    let Some(backend) = backend_opt else {
        let _ = tx
            .send(Ok(named_sse_event("done", json!({"ok": true}))))
            .await;
        drop(tx);
        return Ok(Sse::new(GuardedStream::new(ReceiverStream::new(rx), guard)).keep_alive(KeepAlive::default()));
    };
    let Some(conv_id) = conv_id_opt else {
        let _ = tx
            .send(Ok(named_sse_event("done", json!({"ok": true}))))
            .await;
        drop(tx);
        return Ok(Sse::new(GuardedStream::new(ReceiverStream::new(rx), guard)).keep_alive(KeepAlive::default()));
    };

    let key = conv_key(user.id, &conv_id);
    // 把 decision 当 user 视角的"决策声明"推进 conversation。
    let decision_text = format!(
        "[用户对工具调用 {call_id} 的决策: {decision}]",
    );
    s.console_conversations
        .entry(key.clone())
        .or_insert_with(Vec::new)
        .push(ConsoleMessage {
            role: "user".into(),
            text: decision_text.clone(),
            at: chrono::Utc::now(),
        });

    let mut messages: Vec<ChatMessage> = Vec::new();
    if let Some(conv) = s.console_conversations.get(&key) {
        for m in conv.iter() {
            match m.role.as_str() {
                "user" => messages.push(ChatMessage::user(m.text.clone())),
                "assistant" => messages.push(ChatMessage::assistant(m.text.clone())),
                _ => {}
            }
        }
    }
    if messages.is_empty() {
        messages.push(ChatMessage::user(decision_text.clone()));
    }
    let mut req = ChatRequest {
        model: model_id,
        system: Some(CONSOLE_ASSISTANT_SYSTEM.to_string()),
        messages,
        max_tokens: Some(CONSOLE_ASSISTANT_MAX_TOKENS),
        stream: true,
        ..Default::default()
    };
    // Wave 10-A:console_assistant confirm 路径同样接 extended thinking。
    rpg_llm::merge_thinking_extra(&mut req.extra, rpg_core::config::console_thinking_budget());

    let conv_key_for_task = key;
    let s_for_task = s.clone();
    tokio::spawn(async move {
        let mut full = String::new();
        match backend.stream_chat(req).await {
            Ok(mut stream) => {
                while let Some(item) = stream.next().await {
                    match item {
                        Ok(chunk) => {
                            if let ChatChunk::Text(t) = &chunk {
                                full.push_str(t);
                            }
                            let wire = WireChatChunk::from_chunk(&chunk);
                            let payload =
                                serde_json::to_value(&wire).unwrap_or_else(|_| json!({}));
                            if tx
                                .send(Ok(named_sse_event("chunk", payload)))
                                .await
                                .is_err()
                            {
                                return;
                            }
                        }
                        Err(e) => {
                            let _ = tx
                                .send(Ok(named_sse_event(
                                    "error",
                                    json!({"detail": e.to_string(), "code": "llm_error"}),
                                )))
                                .await;
                            return;
                        }
                    }
                }
            }
            Err(e) => {
                let _ = tx
                    .send(Ok(named_sse_event(
                        "error",
                        json!({"detail": e.to_string(), "code": "llm_error"}),
                    )))
                    .await;
                return;
            }
        }
        if !full.is_empty() {
            s_for_task
                .console_conversations
                .entry(conv_key_for_task)
                .or_insert_with(Vec::new)
                .push(ConsoleMessage {
                    role: "assistant".into(),
                    text: full.clone(),
                    at: chrono::Utc::now(),
                });
        }
        let _ = tx
            .send(Ok(named_sse_event("done", json!({"ok": true}))))
            .await;
    });

    Ok(Sse::new(GuardedStream::new(ReceiverStream::new(rx), guard)).keep_alive(KeepAlive::default()))
}

// ── tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use futures_util::stream;
    use rpg_llm::pipeline::{BackendKind, ChunkStream, LlmBackend, LlmError};

    /// 与 game.rs::tests::MockBackend 同款 — console_assistant 独立 mod 不能复用。
    struct MockBackend {
        chunks: Vec<Result<ChatChunk, LlmError>>,
    }

    #[async_trait]
    impl LlmBackend for MockBackend {
        fn kind(&self) -> BackendKind {
            BackendKind::Openai
        }
        async fn stream_chat<'a>(
            &'a self,
            _req: ChatRequest,
        ) -> Result<ChunkStream<'a>, LlmError> {
            let items: Vec<Result<ChatChunk, LlmError>> = self
                .chunks
                .iter()
                .map(|r| match r {
                    Ok(c) => Ok(c.clone()),
                    Err(e) => Err(LlmError::Other(e.to_string())),
                })
                .collect();
            Ok(Box::pin(stream::iter(items)))
        }
    }

    /// console_assistant chat 累积 assistant 文本 — 只采 ChatChunk::Text。
    #[tokio::test]
    async fn test_console_assistant_text_accumulation() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Text("帮你".into())),
                Ok(ChatChunk::Text("查了：".into())),
                Ok(ChatChunk::Text("3 个存档".into())),
                Ok(ChatChunk::Stop { reason: "end_turn".into() }),
            ],
        };
        let req = ChatRequest::default();
        let mut s = backend.stream_chat(req).await.expect("stream ok");
        let mut full = String::new();
        while let Some(item) = s.next().await {
            if let Ok(ChatChunk::Text(t)) = item {
                full.push_str(&t);
            }
        }
        assert_eq!(full, "帮你查了：3 个存档");
    }

    /// console_assistant chunk 投影为 SSE wire 形态。
    #[tokio::test]
    async fn test_console_assistant_chunk_to_wire() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Text("hi".into())),
                Ok(ChatChunk::Stop { reason: "end_turn".into() }),
            ],
        };
        let req = ChatRequest::default();
        let mut s = backend.stream_chat(req).await.expect("stream ok");
        let mut wires = Vec::new();
        while let Some(item) = s.next().await {
            if let Ok(c) = item {
                wires.push(WireChatChunk::from_chunk(&c));
            }
        }
        assert_eq!(wires.len(), 2);
        assert_eq!(wires[0].kind, "text");
        assert_eq!(wires[0].text.as_deref(), Some("hi"));
        assert_eq!(wires[1].kind, "stop");
    }

    /// confirm 路径 — 决策文本拼接格式不漂（注入会话的 user 消息）。
    #[test]
    fn test_confirm_decision_text_format() {
        let call_id = "call_42".to_string();
        let decision = "approve".to_string();
        let text = format!("[用户对工具调用 {call_id} 的决策: {decision}]");
        assert!(text.contains("call_42"));
        assert!(text.contains("approve"));
    }

    /// system prompt 与 max_tokens 常量保活。
    #[test]
    fn test_console_assistant_constants_alive() {
        assert!(!CONSOLE_ASSISTANT_SYSTEM.is_empty());
        const { assert!(CONSOLE_ASSISTANT_MAX_TOKENS > 0) };
    }
}
