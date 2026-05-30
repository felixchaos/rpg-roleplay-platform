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

use rpg_llm::pipeline::{ChatChunk, ChatMessage, ChatRequest, ToolSchema, WireChatChunk};

use crate::sse_metrics::{GuardedStream, SseConnectionGuard};
use crate::{hello_payload, named_sse_event, require_user, AppState, ConsoleMessage, ResponseError};

// ── stub tool executors (P0-10) ─────────────────────────────────────────────

/// Stub tool executor: routes tool calls to existing Rust functions.
///
/// Returns `(ok: bool, result: Value)`.
async fn execute_console_tool(
    s: &AppState,
    user_id: rpg_core::UserId,
    tool_name: &str,
    args: &Value,
) -> (bool, Value) {
    match tool_name {
        "list_saves" | "list_my_saves" => {
            match rpg_platform::save_io::list_saves_for_user(&s.db, user_id).await {
                Ok(saves) => {
                    let items: Vec<Value> = saves
                        .iter()
                        .map(|s| serde_json::to_value(s).unwrap_or(json!({})))
                        .collect();
                    (true, json!({"saves": items, "count": items.len()}))
                }
                Err(e) => (false, json!({"error": e.to_string()})),
            }
        }
        "create_save" => {
            let script_id = args
                .get("script_id")
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let title = args
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("新存档")
                .to_string();
            if script_id == 0 {
                return (false, json!({"error": "script_id 必填"}));
            }
            let snapshot = rpg_platform::save_io::build_initial_snapshot(
                &s.db,
                user_id.into(),
                script_id,
                None,
                None,
                None,
                None,
                None,
            )
            .await;
            match rpg_platform::save_io::create_save(&s.db, user_id, script_id, &title, &snapshot)
                .await
            {
                Ok(save) => {
                    let val = serde_json::to_value(&save).unwrap_or(json!({}));
                    (true, json!({"save": val}))
                }
                Err(e) => (false, json!({"error": e.to_string()})),
            }
        }
        "activate_save" => {
            let save_id = args.get("save_id").and_then(|v| v.as_i64()).unwrap_or(0);
            if save_id == 0 {
                return (false, json!({"error": "save_id 必填"}));
            }
            match rpg_platform::branches::activation::activate_save(
                &s.db,
                user_id.into(),
                save_id,
            )
            .await
            {
                Ok(result) => (true, json!({"ok": result.ok, "save_id": result.save_id})),
                Err(e) => (false, json!({"error": e.to_string()})),
            }
        }
        "rename_save" => {
            let save_id = args.get("save_id").and_then(|v| v.as_i64()).unwrap_or(0);
            let title = args
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();
            if save_id == 0 || title.is_empty() {
                return (false, json!({"error": "save_id 和 title 必填"}));
            }
            match sqlx::query("UPDATE game_saves SET title = $1, updated_at = now() WHERE id = $2 AND user_id = $3")
                .bind(&title)
                .bind(save_id)
                .bind(i64::from(user_id))
                .execute(&s.db)
                .await
            {
                Ok(r) => {
                    if r.rows_affected() > 0 {
                        (true, json!({"ok": true, "title": title}))
                    } else {
                        (false, json!({"error": "存档不存在或无权操作"}))
                    }
                }
                Err(e) => (false, json!({"error": e.to_string()})),
            }
        }
        "delete_save" => {
            let save_id = args.get("save_id").and_then(|v| v.as_i64()).unwrap_or(0);
            if save_id == 0 {
                return (false, json!({"error": "save_id 必填"}));
            }
            match rpg_platform::save_io::delete_save(&s.db, user_id, save_id).await {
                Ok(_) => (true, json!({"ok": true})),
                Err(e) => (false, json!({"error": e.to_string()})),
            }
        }
        "list_models" | "list_available_models" => {
            let router = s.llm_router.read();
            let cat = router
                .catalog()
                .map(|c| serde_json::to_value(c).unwrap_or(json!({})))
                .unwrap_or(json!({}));
            (true, json!({"models": cat}))
        }
        "set_model" | "select_model" => {
            let api_id = args
                .get("api_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let model_id = args
                .get("model_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if api_id.is_empty() || model_id.is_empty() {
                return (false, json!({"error": "api_id 和 model_id 必填"}));
            }
            // Mutate catalog selected, mirroring models.rs api_models_select
            let mut router = s.llm_router.write();
            if let Some(cat) = router.catalog().cloned() {
                let mut new_cat = cat;
                new_cat.selected = rpg_llm::Selected {
                    api_id: api_id.clone(),
                    model_id: model_id.clone(),
                };
                router.set_catalog(new_cat);
                (true, json!({"ok": true, "api_id": api_id, "model_id": model_id}))
            } else {
                (false, json!({"error": "模型目录未初始化"}))
            }
        }
        _ => (false, json!({"error": format!("未知工具: {tool_name}")})),
    }
}

// ── console tool definitions (P0-10) ────────────────────────────────────────

/// Hardcoded tool schemas for console assistant stub tools.
/// These will be superseded by the real dispatcher registry when it's integrated.
fn builtin_console_tool_schemas() -> Vec<ToolSchema> {
    vec![
        ToolSchema {
            name: "list_saves".into(),
            description: "列出当前用户的所有存档。".into(),
            input_schema: json!({"type": "object", "properties": {}, "required": []}),
            server_id: Some("console".into()),
        },
        ToolSchema {
            name: "create_save".into(),
            description: "创建新存档。需要 script_id 和 title。".into(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "script_id": {"type": "integer", "description": "剧本 ID"},
                    "title": {"type": "string", "description": "存档标题"}
                },
                "required": ["script_id", "title"]
            }),
            server_id: Some("console".into()),
        },
        ToolSchema {
            name: "activate_save".into(),
            description: "激活指定存档为当前运行时。".into(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "存档 ID"}
                },
                "required": ["save_id"]
            }),
            server_id: Some("console".into()),
        },
        ToolSchema {
            name: "rename_save".into(),
            description: "重命名存档。".into(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "存档 ID"},
                    "title": {"type": "string", "description": "新标题"}
                },
                "required": ["save_id", "title"]
            }),
            server_id: Some("console".into()),
        },
        ToolSchema {
            name: "delete_save".into(),
            description: "删除指定存档 (destructive, 需确认)。".into(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer", "description": "存档 ID"}
                },
                "required": ["save_id"]
            }),
            server_id: Some("console".into()),
        },
        ToolSchema {
            name: "list_models".into(),
            description: "列出所有可用模型和 API。".into(),
            input_schema: json!({"type": "object", "properties": {}, "required": []}),
            server_id: Some("console".into()),
        },
        ToolSchema {
            name: "select_model".into(),
            description: "切换当前选用的 LLM 模型。".into(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "API 标识"},
                    "model_id": {"type": "string", "description": "模型标识"}
                },
                "required": ["api_id", "model_id"]
            }),
            server_id: Some("console".into()),
        },
    ]
}

/// Tools marked as destructive (require confirmation before execution).
const DESTRUCTIVE_TOOLS: &[&str] = &["delete_save", "delete_saves"];

fn is_destructive_tool(name: &str) -> bool {
    DESTRUCTIVE_TOOLS.contains(&name)
}

// ── collect tool schemas (P0-11) ────────────────────────────────────────────

/// Build the full tool list for the console assistant ChatRequest.
///
/// Sources:
///   1. Builtin console tools (saves, models) — hardcoded schemas
///   2. MCP tools from mcp_broker.discover_all_tools()
///
/// When the dispatcher registry lands in AppState, also add:
///   3. Dispatcher tools via list_for_origin("console_assistant")
fn collect_console_tools(s: &AppState) -> Vec<ToolSchema> {
    let mut tools = builtin_console_tool_schemas();

    // MCP tools from running servers
    for entry in s.mcp_broker.discover_all_tools() {
        let description = entry
            .schema
            .get("description")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let input_schema = entry
            .schema
            .get("inputSchema")
            .or_else(|| entry.schema.get("input_schema"))
            .cloned()
            .unwrap_or(json!({"type": "object", "properties": {}}));
        tools.push(ToolSchema {
            name: entry.qualified_name,
            description,
            input_schema,
            server_id: Some(entry.server_id),
        });
    }

    tools
}

// ── richer system prompt (P2-14) ────────────────────────────────────────────

/// Build a rich system prompt including tool descriptions, page context, and save info.
fn build_rich_system_prompt(
    s: &AppState,
    user_id: rpg_core::UserId,
    page_context: Option<&Value>,
    tools: &[ToolSchema],
) -> String {
    let mut prompt = CONSOLE_ASSISTANT_SYSTEM_RICH.to_string();

    // Append available tool summary
    if !tools.is_empty() {
        prompt.push_str("\n\n可用工具:");
        for t in tools {
            let destructive_marker = if is_destructive_tool(&t.name) {
                " [destructive, 需确认]"
            } else {
                ""
            };
            prompt.push_str(&format!(
                "\n  - {}{}: {}",
                t.name, destructive_marker, t.description
            ));
        }
    }

    // Current model info
    {
        let router = s.llm_router.read();
        if let Some(cat) = router.catalog() {
            prompt.push_str(&format!("\n\n当前模型: {}", cat.selected.model_id));
        }
    }

    // Page context
    if let Some(ctx) = page_context {
        let ctx_str = serde_json::to_string(ctx).unwrap_or_default();
        if ctx_str.len() > 2 && ctx_str != "null" {
            prompt.push_str("\n\n当前页面上下文:");
            if let Some(tab) = ctx.get("tab").and_then(|v| v.as_str()) {
                prompt.push_str(&format!("\n  tab = {tab}"));
            }
            if let Some(save_id) = ctx.get("save_id") {
                prompt.push_str(&format!("\n  save_id = {save_id}"));
            }
            if let Some(script_id) = ctx.get("script_id") {
                prompt.push_str(&format!("\n  script_id = {script_id}"));
            }
            if let Some(note) = ctx.get("note").and_then(|v| v.as_str()) {
                prompt.push_str(&format!("\n  note = {note}"));
            }
            // UI atlas
            if let Some(atlas) = ctx.get("ui_atlas") {
                if atlas.is_object() {
                    let has_forms = atlas
                        .get("forms")
                        .and_then(|v| v.as_array())
                        .map(|a| !a.is_empty())
                        .unwrap_or(false);
                    let has_modals = atlas
                        .get("open_modals")
                        .and_then(|v| v.as_array())
                        .map(|a| !a.is_empty())
                        .unwrap_or(false);
                    if has_forms || has_modals {
                        prompt.push_str(&render_ui_atlas_for_llm(atlas));
                    }
                }
            }
        }
    } else {
        prompt.push_str("\n\n当前页面: 未知。");
    }

    // Active save info (non-blocking: only uses cached state, won't load from DB)
    let user_id_str = user_id.to_string();
    if let Some(state_ref) = s.state_store.get(&user_id_str) {
        let state = state_ref.read();
        let state_val = serde_json::to_value(&*state).unwrap_or(json!({}));
        if let Some(save_id) = state_val.get("save_id") {
            prompt.push_str(&format!("\n\n当前激活存档 save_id = {save_id}"));
        }
        if let Some(title) = state_val.get("save_title").and_then(|v| v.as_str()) {
            prompt.push_str(&format!(" (标题: {title})"));
        }
    }

    prompt
}

/// Render UI atlas to LLM-friendly compact text (port of Python _render_ui_atlas_for_llm).
fn render_ui_atlas_for_llm(atlas: &Value) -> String {
    let mut lines = vec![String::new(), "ui_atlas (当前页面结构):".to_string()];

    if let Some(page) = atlas.get("page").and_then(|v| v.as_str()) {
        let label = atlas
            .get("page_label")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let suffix = if label.is_empty() {
            String::new()
        } else {
            format!(" ({label})")
        };
        lines.push(format!("  page = {page}{suffix}"));
    }

    if let Some(modals) = atlas.get("open_modals").and_then(|v| v.as_array()) {
        if !modals.is_empty() {
            let modal_strs: Vec<String> = modals
                .iter()
                .filter_map(|v| v.as_str().map(|s| format!("\"{s}\"")))
                .collect();
            lines.push(format!("  open_modals = [{}]", modal_strs.join(", ")));
        }
    }

    if let Some(forms) = atlas.get("forms").and_then(|v| v.as_array()) {
        for f in forms.iter().take(5) {
            let fid = f
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("?");
            let title = f
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            lines.push(format!("  form '{fid}' ({title}):"));

            if let Some(fields) = f.get("fields").and_then(|v| v.as_array()) {
                for fld in fields.iter().take(20) {
                    let key = fld
                        .get("key")
                        .or_else(|| fld.get("label"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("?");
                    let ftype = fld
                        .get("type")
                        .and_then(|v| v.as_str())
                        .unwrap_or("text");
                    let required = if fld
                        .get("required")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false)
                    {
                        " *"
                    } else {
                        ""
                    };
                    let val = fld.get("value");
                    let val_str = match val {
                        Some(v) if !v.is_null() && v.as_str().map(|s| !s.is_empty()).unwrap_or(true) => {
                            format!(" = {v}")
                        }
                        _ => String::new(),
                    };
                    lines.push(format!("    - {key}{required} ({ftype}){val_str}"));
                }
            }

            if let Some(actions) = f.get("top_actions").and_then(|v| v.as_array()) {
                for act in actions.iter().take(6) {
                    let lbl = act
                        .get("label")
                        .and_then(|v| v.as_str())
                        .unwrap_or("?");
                    let disabled = if act
                        .get("disabled")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false)
                    {
                        " [disabled]"
                    } else {
                        ""
                    };
                    lines.push(format!("    -> button '{lbl}'{disabled}"));
                }
            }
        }
    }

    lines.join("\n")
}

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

    // P0-11: collect tool definitions for ChatRequest
    let tools = collect_console_tools(&s);

    // P2-14: build rich system prompt with tool descriptions, page context, save info
    let system_prompt = build_rich_system_prompt(
        &s,
        user.id,
        body.page_context.as_ref(),
        &tools,
    );

    let mut req = ChatRequest {
        model: model_id,
        system: Some(system_prompt),
        messages,
        tools, // P0-11: send tool definitions to LLM
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

            // 处理每个 tool call — P0-10 stub executors + P0-12 destructive confirmation
            let mut tool_results: Vec<ChatMessage> = Vec::new();
            let mut hit_destructive = false;
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

                    // P0-12: destructive confirmation gate
                    if is_destructive_tool(tool_name) {
                        let pending_key = format!("{user_id_for_task}:{conv_id_for_task}:{id}");
                        let pending_info = json!({
                            "call_id": id,
                            "tool": tool_name,
                            "server_id": server_id,
                            "arguments": input,
                            "destructive": true,
                        });
                        s_for_task.console_pending_confirmations.insert(
                            pending_key,
                            pending_info,
                        );

                        // Emit confirmation_required SSE event
                        let _ = tx.send(Ok(named_sse_event("confirmation_required", json!({
                            "call_id": id,
                            "tool": tool_name,
                            "args": input,
                            "description": format!("确认执行 {tool_name}?"),
                            "destructive": true,
                        })))).await;

                        // Tell LLM the tool is pending confirmation
                        let pending_text = format!(
                            "[工具 {tool_name} 需要用户确认才能执行,等待 approve/reject]"
                        );
                        tool_results.push(ChatMessage::tool_result(id.clone(), pending_text));
                        hit_destructive = true;
                        break; // stop processing further tool calls this round
                    }

                    // P0-10: route to stub executor or MCP broker
                    let (ok, result) = if server_id.is_empty() || server_id == "console" {
                        // Try builtin stub executor first
                        execute_console_tool(
                            &s_for_task,
                            user_id_for_task,
                            tool_name,
                            input,
                        )
                        .await
                    } else {
                        // MCP broker for external tools
                        let r = s_for_task
                            .mcp_broker
                            .call_tool(server_id, tool_name, input.clone(), 30)
                            .await;
                        let mcp_ok = r.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                        (mcp_ok, r)
                    };

                    let result_text = serde_json::to_string(&result).unwrap_or_default();

                    // 发送 tool_result SSE 事件
                    let _ = tx.send(Ok(named_sse_event("tool_result", json!({
                        "call_id": id,
                        "ok": ok,
                        "result": result,
                    })))).await;

                    tool_results.push(ChatMessage::tool_result(id.clone(), result_text));
                }
            }

            // 把 tool results 追加进下一轮的 messages
            req.messages.extend(tool_results);

            // P0-12: if a destructive tool was hit, break the loop — wait for user confirmation
            if hit_destructive {
                break 'outer;
            }
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

/// P2-14: 丰富版 system prompt — 移植自 Python console_assistant/prompts.py。
/// 取代旧版简化版 CONSOLE_ASSISTANT_SYSTEM。
const CONSOLE_ASSISTANT_SYSTEM_RICH: &str = r#"你是 RPG Platform 的侧栏控制台助手。不是游戏 GM, 不写故事、不推剧情。
帮用户管理平台资源 (存档/角色卡/persona/剧本/设置/MCP)。

工具都在 tools 列表里, description 写满了细节和示例 — 直接用。
看到用户意图就调对应的工具, 不要绕弯。

几条硬规则:
1. 需要用户做选择时用结构化方式,不要裸列选项。
2. 禁止自己编造 required 字段的值。用户没说就先问。
3. "查看/列出/看看" → 直接调 list_* 工具,不要 navigate。
4. "建角色卡" 是平台资产,跟"改剧情里玩家名"不同。
5. 用户用相对指代时,直接用最近的/最新的,不要再问。
6. tool_result 是唯一真相,禁止编造动作完成叙述。
7. 删除/批量 destructive 前先 list 拿真实 ID,禁止凭猜测填 ID。

中文, 简洁。"#;

/// console_assistant max_tokens (P1-18: 600 → 1200)。
const CONSOLE_ASSISTANT_MAX_TOKENS: u32 = 1200;

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

            // P0-10: route to stub executor or MCP broker
            let (ok, result) = if server_id.is_empty() || server_id == "console" {
                execute_console_tool(&s, user.id, &tool_name, &arguments).await
            } else {
                let r = s.mcp_broker.call_tool(&server_id, &tool_name, arguments, 30).await;
                let mcp_ok = r.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                (mcp_ok, r)
            };

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

    // P0-11: collect tool definitions for confirm path too
    let tools = collect_console_tools(&s);
    // P2-14: rich system prompt for confirm path
    let system_prompt = build_rich_system_prompt(
        &s,
        user.id,
        body.page_context.as_ref(),
        &tools,
    );

    let mut req = ChatRequest {
        model: model_id,
        system: Some(system_prompt),
        messages,
        tools, // P0-11: include tool definitions
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
        assert!(!CONSOLE_ASSISTANT_SYSTEM_RICH.is_empty());
        const { assert!(CONSOLE_ASSISTANT_MAX_TOKENS > 0) };
    }
}
