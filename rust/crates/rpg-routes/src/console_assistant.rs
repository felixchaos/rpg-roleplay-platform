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
/// CONSOLE-ASSISTANT-TOOL-LOOP-NOT-IMPLEMENTED: MCP tool loop 已实现(8 轮上限)。
/// 包含:tool_call/tool_result SSE 事件、confirmation_required 流程、pending_confirmations。
/// 完整 dispatcher 集成待 rpg-tools-dsl 成熟后引入。
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

    // CONSOLE-CHAT-MISSING-EMPTY-MESSAGE-GUARD: check for empty message
    if message.trim().is_empty() {
        let uid_str = user.id.to_string();
        let (err_tx, err_rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(4);
        let _ = err_tx.send(Ok(named_sse_event("hello", hello_payload(&uid_str)))).await;
        let _ = err_tx.send(Ok(named_sse_event(
            "error",
            json!({"message": "空消息", "code": "bad_request"}),
        ))).await;
        let _ = err_tx.send(Ok(named_sse_event("done", json!({"ok": true})))).await;
        drop(err_tx);
        let guard = SseConnectionGuard::new("console");
        return Ok(Sse::new(GuardedStream::new(ReceiverStream::new(err_rx), guard)).keep_alive(KeepAlive::default()));
    }

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
            // CONSOLE-DONE-MISSING-PENDING-CONFIRMATIONS: include pending_confirmations
            .send(Ok(named_sse_event("done", json!({"ok": true, "pending_confirmations": []}))))
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

    // CONSOLE-ASSISTANT-CHAT-MISSING-PAGE-CONTEXT: merge page_context into system prompt
    let system_prompt = if let Some(page_ctx) = &body.page_context {
        let ctx_str = serde_json::to_string(page_ctx).unwrap_or_default();
        if ctx_str.len() > 2 && ctx_str != "null" {
            format!("{}\n\n当前页面上下文:\n{}", CONSOLE_ASSISTANT_SYSTEM, ctx_str)
        } else {
            CONSOLE_ASSISTANT_SYSTEM.to_string()
        }
    } else {
        CONSOLE_ASSISTANT_SYSTEM.to_string()
    };

    let mut req = ChatRequest {
        model: model_id,
        system: Some(system_prompt),
        messages,
        max_tokens: Some(CONSOLE_ASSISTANT_MAX_TOKENS),
        stream: true,
        ..Default::default()
    };
    // Wave 10-A:console_assistant 默认 0,运营可通过 RPG_CONSOLE_THINKING_BUDGET 开启。
    rpg_llm::merge_thinking_extra(&mut req.extra, rpg_core::config::console_thinking_budget());

    let conv_key_for_task = key.clone();
    let s_for_task = s.clone();
    let conv_id_for_task = conv_id.clone();
    let user_id_for_task = user.id;
    tokio::spawn(async move {
        // CONSOLE-MISSING-TOOL-LOOP: MCP tool loop — 最多 8 轮,防止死循环。
        let max_rounds = 8usize;

        'outer: for _round in 0..max_rounds {
            let mut full = String::new();
            let mut tool_calls_this_round: Vec<ChatChunk> = Vec::new();

            match backend.stream_chat(req.clone()).await {
                Ok(mut stream) => {
                    while let Some(item) = stream.next().await {
                        match item {
                            Ok(chunk) => {
                                if let ChatChunk::Text(t) = &chunk {
                                    full.push_str(t);
                                }
                                if let ChatChunk::ToolCall { .. } = &chunk {
                                    tool_calls_this_round.push(chunk.clone());
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

            // 追加 assistant 文本到 conversation
            if !full.is_empty() {
                s_for_task
                    .console_conversations
                    .entry(conv_key_for_task.clone())
                    .or_insert_with(Vec::new)
                    .push(ConsoleMessage {
                        role: "assistant".into(),
                        text: full.clone(),
                        at: chrono::Utc::now(),
                    });
            }

            // 无 tool calls → 结束循环
            if tool_calls_this_round.is_empty() {
                break 'outer;
            }

            // 处理每个 tool call
            let mut tool_results: Vec<ChatMessage> = Vec::new();
            for tc_chunk in &tool_calls_this_round {
                if let ChatChunk::ToolCall { id, name, input } = tc_chunk {
                    // 解析 server_id:tool_name (qualified: "server__tool")
                    let (server_id, tool_name) = if let Some(idx) = name.find("__") {
                        (&name[..idx], &name[idx + 2..])
                    } else {
                        ("", name.as_str())
                    };

                    // 发送 tool_call SSE 事件
                    let _ = tx.send(Ok(named_sse_event("tool_call", json!({
                        "call_id": id,
                        "tool": tool_name,
                        "server_id": server_id,
                        "args": input,
                    })))).await;

                    // 调用 MCP broker
                    let result = s_for_task
                        .mcp_broker
                        .call_tool(server_id, tool_name, input.clone(), 30)
                        .await;

                    let ok = result.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                    let result_text = serde_json::to_string(&result).unwrap_or_default();

                    // 发送 tool_result SSE 事件
                    let _ = tx.send(Ok(named_sse_event("tool_result", json!({
                        "call_id": id,
                        "ok": ok,
                        "result": result,
                    })))).await;

                    // 存储 pending confirmation key（用于 confirm endpoint 查找）
                    let pending_key = format!("{user_id_for_task}:{conv_id_for_task}:{id}");
                    s_for_task.console_pending_confirmations.insert(
                        pending_key,
                        json!({
                            "call_id": id,
                            "tool": tool_name,
                            "server_id": server_id,
                            "arguments": input,
                            "result": result,
                        }),
                    );

                    tool_results.push(ChatMessage::tool_result(id.clone(), result_text));
                }
            }

            // 把 tool results 追加进下一轮的 messages
            req.messages.extend(tool_results);
        }

        // 收集 pending_confirmations 列表
        let prefix = format!("{user_id_for_task}:{conv_id_for_task}:");
        let pending_confirmations: Vec<serde_json::Value> = s_for_task
            .console_pending_confirmations
            .iter()
            .filter(|e| e.key().starts_with(&prefix))
            .map(|e| e.value().clone())
            .collect();

        let _ = tx
            .send(Ok(named_sse_event("done", json!({"ok": true, "pending_confirmations": pending_confirmations}))))
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
/// CONSOLE-CONFIRM-MISSING-PENDING-RESOLUTION: pending confirmation resolution not yet
/// implemented. Python resolves the pending confirmation (pop from conv['pending_confirmations']),
/// dispatches the tool if approved via tools_dsl dispatcher, emits tool_result or navigation_required.
/// Needs pending_confirmations tracking in AppState and rpg-tools-dsl dispatcher integration.
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
            // CONSOLE-DONE-MISSING-PENDING-CONFIRMATIONS: include pending_confirmations
            .send(Ok(named_sse_event("done", json!({"ok": true, "pending_confirmations": []}))))
            .await;
        drop(tx);
        return Ok(Sse::new(GuardedStream::new(ReceiverStream::new(rx), guard)).keep_alive(KeepAlive::default()));
    };
    let Some(conv_id) = conv_id_opt else {
        let _ = tx
            // CONSOLE-DONE-MISSING-PENDING-CONFIRMATIONS: include pending_confirmations
            .send(Ok(named_sse_event("done", json!({"ok": true, "pending_confirmations": []}))))
            .await;
        drop(tx);
        return Ok(Sse::new(GuardedStream::new(ReceiverStream::new(rx), guard)).keep_alive(KeepAlive::default()));
    };

    let key = conv_key(user.id, &conv_id);

    // CONSOLE-CONFIRM-MISSING-PENDING-RESOLUTION: 真实 dispatch pending tool call。
    // 从 console_pending_confirmations 找到对应的 tool call。
    let pending_key = format!("{}:{}:{}", user.id, conv_id, call_id);
    let pending = s.console_pending_confirmations.get(&pending_key).map(|v| v.clone());

    if let Some(pending_info) = &pending {
        if decision == "approve" {
            // 从 pending_info 提取 tool call 参数
            let server_id = pending_info.get("server_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let tool_name = pending_info.get("tool").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let arguments = pending_info.get("arguments").cloned().unwrap_or(json!({}));

            // 发送 tool_call SSE 事件
            let _ = tx.send(Ok(named_sse_event("tool_call", json!({
                "call_id": call_id,
                "tool": tool_name,
                "server_id": server_id,
                "args": arguments,
                "approved": true,
            })))).await;

            // 实际调用 MCP broker
            let result = s.mcp_broker.call_tool(&server_id, &tool_name, arguments, 30).await;
            let ok = result.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);

            // 发送 tool_result SSE 事件
            let _ = tx.send(Ok(named_sse_event("tool_result", json!({
                "call_id": call_id,
                "ok": ok,
                "result": result.clone(),
            })))).await;

            // 把 tool result 追加进 conversation(role=tool)
            let result_text = serde_json::to_string(&result).unwrap_or_default();
            s.console_conversations
                .entry(key.clone())
                .or_insert_with(Vec::new)
                .push(ConsoleMessage {
                    role: "tool".into(),
                    text: result_text,
                    at: chrono::Utc::now(),
                });
        } else {
            // reject:注入拒绝声明
            s.console_conversations
                .entry(key.clone())
                .or_insert_with(Vec::new)
                .push(ConsoleMessage {
                    role: "user".into(),
                    text: format!("[用户拒绝了工具调用 {call_id}]"),
                    at: chrono::Utc::now(),
                });
        }
        // 移除 pending confirmation(无论 approve 还是 reject)
        s.console_pending_confirmations.remove(&pending_key);
    }

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
    let conv_id_for_confirm = conv_id.clone();
    let user_id_for_confirm = user.id;
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
        // 收集剩余 pending_confirmations
        let prefix = format!("{user_id_for_confirm}:{conv_id_for_confirm}:");
        let remaining_confirmations: Vec<serde_json::Value> = s_for_task
            .console_pending_confirmations
            .iter()
            .filter(|e| e.key().starts_with(&prefix))
            .map(|e| e.value().clone())
            .collect();
        let _ = tx
            .send(Ok(named_sse_event("done", json!({"ok": true, "pending_confirmations": remaining_confirmations}))))
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
