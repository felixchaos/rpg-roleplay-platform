//! game.py → game.rs — 游戏核心流程路由
//! POST /api/new                  — 创建新存档
//! POST /api/opening              — SSE 开场白
//! GET  /api/chat/context-breakdown — 上下文 breakdown
//! POST /api/chat/estimate        — 实时上下文预估
//! POST /api/chat                 — 主聊天 SSE
//! POST /api/stop                 — 打断当前 chat
//! POST /api/save                 — 保存存档

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
use futures_util::stream::{self, Stream, StreamExt};
use http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio_stream::wrappers::ReceiverStream;

use rpg_llm::pipeline::{
    ChatChunk, ChatMessage, ChatRequest as LlmChatRequest, WireChatChunk,
};
use rpg_platform::quota::{self, QuotaConfig, QuotaError};
use rpg_state::GameState;

use crate::{hello_payload, named_sse_event, require_user, user_id_or_anon, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/new", post(api_new))
        .route("/api/opening", post(api_opening))
        .route("/api/chat/estimate", post(api_chat_estimate))
        .route("/api/chat/context-breakdown", get(api_context_breakdown))
        .route("/api/chat", post(api_chat))
        .route("/api/stop", post(api_stop))
        .route("/api/save", post(api_save))
}

/// 非 SSE 路由(供 build_regular_routes 使用,排除 /api/chat 和 /api/opening)。
pub fn regular_router() -> Router<AppState> {
    Router::new()
        .route("/api/new", post(api_new))
        .route("/api/chat/estimate", post(api_chat_estimate))
        .route("/api/chat/context-breakdown", get(api_context_breakdown))
        .route("/api/stop", post(api_stop))
        .route("/api/save", post(api_save))
}


// ── request / response types ────────────────────────────────────────────────

#[derive(Debug, Deserialize, Default)]
pub struct NewGameRequest {
    pub name: Option<String>,
    pub role: Option<String>,
    pub background: Option<String>,
    pub persona_id: Option<i64>,
    pub user_card_id: Option<i64>,
    pub script_card_id: Option<i64>,
    pub script_id: Option<i64>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ChatRequest {
    pub message: Option<String>,
    /// 旧版前端字段 fallback
    pub text: Option<String>,
    pub attachments: Option<Vec<Value>>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ChatEstimateRequest {
    pub message: Option<String>,
    pub include_retrieval: Option<bool>,
}

// ── handlers ─────────────────────────────────────────────────────────────────

/// POST /api/new — 创建新存档
///
/// 把现有 state 重置成空白存档(可选地写 player 基础字段)。
/// 注:剧本角色卡 / persona / user_card 查询走 rpg-platform,本翻译期暂不接,
/// 优先支持 body.name/role/background 这条主路径。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_new(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<NewGameRequest>>,
) -> Result<Response, ResponseError> {
    let body = body.map(|Json(b)| b).unwrap_or_default();
    // 写状态路由:强鉴权,匿名 → 401。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    {
        let mut st = shared.write();
        *st = GameState::new(user_id.clone());
        // 写 player 三件套
        let name = body
            .name
            .as_deref()
            .map(str::trim)
            .filter(|x| !x.is_empty())
            .unwrap_or("无名者")
            .to_string();
        let role = body
            .role
            .as_deref()
            .map(str::trim)
            .filter(|x| !x.is_empty())
            .unwrap_or("未指定")
            .to_string();
        let background = body
            .background
            .as_deref()
            .map(str::trim)
            .filter(|x| !x.is_empty())
            .unwrap_or("")
            .to_string();
        let _ = st.set_path("player.name", Value::String(name));
        let _ = st.set_path("player.role", Value::String(role));
        let _ = st.set_path("player.background", Value::String(background));
        let _ = st.set_path("is_new", Value::Bool(false));
    }
    // 6C-1 Arc 快照:读路径不再深拷贝整树,snapshot() 仅 inc Arc refcount。
    // api_new 刚写过 state(touch 已让快照缓存失效),此处重建一次后返回。
    let (data, version) = {
        let st = shared.read();
        (st.snapshot(), st.version)
    };
    Ok(Json(json!({
        "ok": true,
        "state": data,
        "version": version,
    }))
    .into_response())
}

/// POST /api/opening — SSE 开场白流(Wave 6-A:真接 LLM stream)
///
/// 流程:
///   1. 鉴权 + 取 user GameState 快照。
///   2. 构造 system + opening user prompt(`OPENING_SYSTEM` / `OPENING_USER`)。
///   3. 取 `llm_router.current_backend()`;backend 未注册 → 退化成 stub
///      (产 hello + state_change + 空 chunk + done),保证前端不卡。
///   4. 真接 `backend.stream_chat(req)`,每个 [`ChatChunk`] 投影为 SSE event。
///   5. 流末 append 完整 opening 到 `state.history` 并 emit done(带最新 snapshot)。
///   6. 错误转 SSE error 帧。
///
/// 不包含:context_engine 调度(Python 走 retrieve_context + _build_turn_context),
/// extractor / structured_updates 解析(只追加 raw assistant 文本)。
/// 这两块由 Wave 6-B 后续 / rpg-agents `GmEvent` 引入。
#[tracing::instrument(skip(s, headers), fields(user_id))]
pub(crate) async fn api_opening(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    // 触达 LLM 路由:强鉴权,匿名严禁触达 LLM → 401。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;

    // 取 backend(若无 catalog/无注册则 stub fallback,不报错以兼容空环境)。
    let backend_opt = s.llm_router.read().current_backend().ok();

    // 没 backend → 老 stub 路径(同旧行为)。
    let Some(backend) = backend_opt else {
        let state_data = shared.read().snapshot();
        let events = vec![
            Ok::<_, Infallible>(named_sse_event("hello", hello_payload(&user_id))),
            Ok(named_sse_event(
                "state_change",
                json!({"phase":"generating","label":"GM 构思开场中(stub)…"}),
            )),
            Ok(named_sse_event("chunk", json!({"text":""}))),
            Ok(named_sse_event(
                "done",
                json!({"status": {"state": state_data}, "interrupted": false}),
            )),
        ];
        let stream = stream::iter(events).left_stream();
        return Ok(Sse::new(stream).keep_alive(KeepAlive::default()));
    };

    // 取 catalog 的 selected.model_id(空则让 router 兜底)。
    let model = s
        .llm_router
        .read()
        .catalog()
        .map(|c| c.selected.model_id.clone())
        .unwrap_or_default();

    // 构造 system + opening prompt(简化版,不接 module manifest world section)。
    let summary = rpg_agents::common::state_short_summary(&shared.read());
    let system = OPENING_SYSTEM.to_string();
    let user_msg = format!(
        "【当前剧情状态】\n{summary}\n\n{OPENING_USER}",
    );
    let req = LlmChatRequest {
        model,
        system: Some(system),
        messages: vec![ChatMessage::user(user_msg)],
        max_tokens: Some(OPENING_MAX_TOKENS),
        stream: true,
        ..Default::default()
    };

    // 把流处理委托给 helper:返回 mpsc::Receiver<Event>。
    let state_handle = shared.clone();
    let user_id_clone = user_id.clone();
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(32);
    // 首帧 hello。
    let _ = tx
        .send(Ok(named_sse_event("hello", hello_payload(&user_id))))
        .await;
    let _ = tx
        .send(Ok(named_sse_event(
            "state_change",
            json!({"phase":"generating","label":"GM 构思开场中…"}),
        )))
        .await;

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
        // 追加 assistant 消息到 history。
        if !full.is_empty() {
            let mut st = state_handle.write();
            let _ = st.append_to_path(
                "history",
                json!({"role": "assistant", "content": full.clone()}),
            );
        }
        // done — 带最新 snapshot。
        let state_data = state_handle.read().snapshot();
        let _ = tx
            .send(Ok(named_sse_event(
                "done",
                json!({"status": {"state": state_data}, "interrupted": false}),
            )))
            .await;
        // user_id_clone 仅用作 spawn task 跟踪(防 borrow drop)。
        let _ = user_id_clone;
    });

    let stream = ReceiverStream::new(rx).right_stream();
    Ok(Sse::new(stream).keep_alive(KeepAlive::default()))
}

/// opening 用的 system prompt — 简化版,够 LLM 知道自己是 GM 即可。
const OPENING_SYSTEM: &str = "你是这局 TRPG 的 Game Master。请用第二人称叙事,聚焦于场景、五感与玩家可触发的下一步。语气克制,留白让玩家选择。";

/// opening 用的 user prompt — 触发 GM 写开场。
const OPENING_USER: &str = "请生成一段开场白(150~250 字),描述玩家角色刚醒来时的所见所感。结尾要让玩家自然引出第一个动作。";

/// opening max_tokens。
const OPENING_MAX_TOKENS: u32 = 600;

/// POST /api/chat/estimate — 实时上下文预估
///
/// 本翻译期没接 platform_app.usage,给一个极轻量估算:
/// 输入 = system(1200) + history_char/3 + retrieval(800 if include) + message_char/3。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
async fn api_chat_estimate(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ChatEstimateRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let message = body.message.unwrap_or_default();
    let include_retrieval = body.include_retrieval.unwrap_or(true);
    let history_text = {
        let st = shared.read();
        st.data
            .history
            .iter()
            .filter_map(|m| m.get("content").and_then(|v| v.as_str()))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let est = |s: &str| (s.chars().count() / 3) as i64;
    let system_est: i64 = 1200;
    let retrieval_est: i64 = if include_retrieval { 800 } else { 0 };
    let history_est = est(&history_text);
    let input_tokens = system_est + history_est + retrieval_est + est(&message);
    let output_estimate: i64 = 600;
    let ctx_max: i64 = 1_000_000;
    let ctx_pct = (input_tokens as f64 * 100.0 / ctx_max as f64 * 10.0).round() / 10.0;
    let total = input_tokens + output_estimate;
    let will_overflow = total > ctx_max;
    Ok(Json(json!({
        "ok": true,
        "api_id": "",
        "model": "",
        "context_used": input_tokens,
        "context_max": ctx_max,
        "context_pct": ctx_pct,
        "estimated_output_tokens": output_estimate,
        "estimated_total_tokens": total,
        "will_overflow": will_overflow,
        "breakdown": {
            "system_prompt": system_est,
            "history": history_est,
            "retrieval_budget": retrieval_est,
            "current_input": est(&message),
        },
        "headroom_tokens": (ctx_max - input_tokens - output_estimate).max(0),
    }))
    .into_response())
}

/// GET /api/chat/context-breakdown — 返回上次 context 各层 token 分布
///
/// 直接读 state.memory.last_context,layer 分类映射放到 inline 表;
/// 没有 last_context 时返回 0 token + 单一 free 项。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_context_breakdown(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let last_ctx = {
        let st = shared.read();
        Value::Object(st.data.memory.last_context.clone())
    };
    let total_tokens = last_ctx
        .get("estimated_tokens")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let layers = last_ctx
        .get("layers")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // category 累加
    let category_of = |id: &str| -> &'static str {
        match id {
            "recent_chat" | "user_input" => "history",
            "rag" | "novel_retrieval" => "retrieved_chunks",
            "fact_groups" | "hypotheses" | "memory" => "memory_facts",
            "player_card" | "npc_cards" | "novel_characters" => "character_cards",
            "worldbook" | "novel_worldbook" | "module_worldbook" => "worldbook",
            "novel_timeline" | "runtime_phase_digests" => "phase_digests",
            _ => "system_prompt",
        }
    };
    let mut cat_tokens: std::collections::HashMap<&'static str, i64> = Default::default();
    for layer in &layers {
        let id = layer.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let tok = layer
            .get("estimated_tokens")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        *cat_tokens.entry(category_of(id)).or_insert(0) += tok;
    }

    let order: &[(&str, &str, &str)] = &[
        ("history", "对话历史", "#4f8ef7"),
        ("system_prompt", "系统提示", "#9b6bdf"),
        ("retrieved_chunks", "RAG 召回", "#2bae8a"),
        ("memory_facts", "长期记忆", "#e6a817"),
        ("character_cards", "角色卡", "#e05c7a"),
        ("worldbook", "世界书", "#3dbad4"),
        ("phase_digests", "阶段摘要", "#f07a3c"),
        ("tools", "工具/MCP", "#8899aa"),
    ];
    let ctx_limit: i64 = 1_000_000;
    let mut used_sum = 0i64;
    let mut breakdown = Vec::new();
    for (key, label, color) in order {
        let tok = cat_tokens.get(key).copied().unwrap_or(0);
        used_sum += tok;
        let pct = if ctx_limit > 0 {
            (tok as f64 * 100.0 / ctx_limit as f64 * 10.0).round() / 10.0
        } else {
            0.0
        };
        breakdown.push(json!({"key": key, "label": label, "tokens": tok, "pct": pct, "color": color}));
    }
    let free = (ctx_limit - used_sum).max(0);
    let free_pct = if ctx_limit > 0 {
        (free as f64 * 100.0 / ctx_limit as f64 * 10.0).round() / 10.0
    } else {
        0.0
    };
    breakdown.push(
        json!({"key": "free", "label": "剩余空间", "tokens": free, "pct": free_pct, "color": "#555e6a"}),
    );
    Ok(Json(json!({
        "ok": true,
        "total_tokens": if total_tokens > 0 { total_tokens } else { used_sum },
        "ctx_limit": ctx_limit,
        "breakdown": breakdown,
    }))
    .into_response())
}

/// POST /api/chat — 主聊天 SSE(Wave 6-A:真接 LLM stream)
///
/// Python 端是 5 阶段 chat_pipeline。Rust 翻译期 GameMaster 链路 (rpg-agents + rpg-llm
/// router 注入) 还在搭,本 handler 只做:
///   1. 鉴权 + 空消息校验
///   2. 配额闸(预估 input + hard_max_tokens)
///   3. 写 user 消息进 history
///   4. 取 current_backend(无 backend → 老 stub fallback)→ 真接 stream_chat
///   5. 逐 chunk 转 SSE,append assistant 文本到 history,emit done(带最新 snapshot)
///   6. 流末检 cluster::is_stop_requested 决定 interrupted 字段
///
/// 尚未引入:context_engine 跑 retrieval、worldbook_consulting 中间事件、
/// extractor → ops apply、rules preflight、persist_chat_turn。这些归 Wave 6-B。
#[tracing::instrument(skip(s, headers, body), fields(user_id))]
pub(crate) async fn api_chat(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<ChatRequest>>,
) -> Response {
    let body = body.map(|Json(b)| b).unwrap_or_default();

    // ── 强鉴权:匿名严禁触达 LLM → 401。本 handler 返回裸 Response,手动渲染。
    let user = match require_user(&s, &headers).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));

    let message = body
        .message
        .or(body.text)
        .unwrap_or_default()
        .trim()
        .to_string();
    if message.is_empty() {
        let events = vec![
            Ok::<_, Infallible>(named_sse_event("hello", hello_payload(&user_id))),
            Ok(named_sse_event(
                "error",
                json!({"detail":"空消息","code":"bad_request"}),
            )),
        ];
        return Sse::new(stream::iter(events)).into_response();
    }

    // ── 调 LLM 前过配额闸:预算 / 日配额 / 速率 / 并发。失败 → 429 + Retry-After。
    let cfg = QuotaConfig::from_env();
    // 预估总 token:输入按字符粗估 + 预期 output 上限(hard cap)。
    let est_input = rpg_platform::usage::estimate_input_tokens(&message);
    let est_tokens = est_input + cfg.hard_max_tokens as i64;
    // 当前 chat 链路尚未注入真实 model 选择,先用占位 api/model(record_actual 也用同值)。
    let api_id = "anthropic";
    let model = "claude-stub";
    let grant = match quota::check_and_reserve(
        &s.db, &cfg, user.id, api_id, model, est_tokens,
    )
    .await
    {
        Ok(g) => g,
        Err(e) => return quota_error_response(&user_id, e),
    };

    // 清零该 user 的 stop notify(重新建一个,旧的 awaiter 自动 drop)。
    s.stop_events.remove(&user_id);
    let _stop = s.stop_notify(&user_id);

    // 6C-1 跨 pod stop:为本次 chat 分配 run_id 并登记;清掉上一轮可能残留的
    // 跨进程 stop_signals(避免新 run 一上来就被旧信号打断)。
    let run_id = s.next_run_id(user.id);
    rpg_platform::cluster::clear_stop(&s.db, user.id.get(), run_id).await;

    let shared = s.state_store.get_or_create(&user_id).await;
    // 写入 user 消息到 history
    {
        let mut st = shared.write();
        let _ = st.append_to_path(
            "history",
            json!({"role": "user", "content": message.clone()}),
        );
    }

    // ── 构造 LLM 请求 ──────────────────────────────────────────────
    // history → ChatMessage(从 state.data.history 翻译;过滤非 user/assistant)。
    let mut messages: Vec<ChatMessage> = {
        let st = shared.read();
        st.data
            .history
            .iter()
            .filter_map(|m| {
                let role = m.get("role")?.as_str()?;
                let content = m.get("content")?.as_str().unwrap_or("");
                match role {
                    "user" => Some(ChatMessage::user(content)),
                    "assistant" => Some(ChatMessage::assistant(content)),
                    _ => None,
                }
            })
            .collect()
    };
    // history 已含本轮 user(刚 append),不再 push 重复。
    if messages.is_empty() {
        // 兜底:历史空时也得有当前 user 消息(理论上不该走到)。
        messages.push(ChatMessage::user(message.clone()));
    }

    // 取 backend + selected model;无 backend → 退化老 stub 路径(保持兼容)。
    let backend_opt = s.llm_router.read().current_backend().ok();
    let model_id = s
        .llm_router
        .read()
        .catalog()
        .map(|c| c.selected.model_id.clone())
        .unwrap_or_default();

    // record_actual 闭包:仅在 LLM 路径走到末尾或失败时调一次。
    // 为了让 spawned task 拿走 grant,把 db pool 也克隆进去。
    let db = s.db.clone();
    let user_id_str = user_id.clone();
    let state_handle = shared.clone();

    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(32);
    // 首帧。
    let _ = tx
        .send(Ok(named_sse_event("hello", hello_payload(&user_id))))
        .await;
    let _ = tx
        .send(Ok(named_sse_event(
            "state_change",
            json!({"phase":"generating","label":"GM 思考中…"}),
        )))
        .await;

    // 无 backend → stub fallback:只 emit 一个空 chunk + done。
    let Some(backend) = backend_opt else {
        let state_after = shared.read().snapshot();
        let actual = rpg_platform::usage::UsageBreakdown {
            input_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
            output_tokens: 0,
            cached_input_tokens: 0,
            reasoning_tokens: 0,
            total_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
        };
        quota::record_actual(&db, grant, None, None, &actual, est_input as i32, 1_000_000).await;
        let _ = tx
            .send(Ok(named_sse_event("chunk", json!({"text":""}))))
            .await;
        let _ = tx
            .send(Ok(named_sse_event(
                "done",
                json!({"status": {"state": state_after}, "interrupted": false}),
            )))
            .await;
        drop(tx);
        let stream = ReceiverStream::new(rx);
        return Sse::new(stream).keep_alive(KeepAlive::default()).into_response();
    };

    let req = LlmChatRequest {
        model: model_id,
        system: Some(CHAT_SYSTEM.to_string()),
        messages,
        max_tokens: Some(CHAT_MAX_TOKENS),
        stream: true,
        ..Default::default()
    };

    // 在 task 内跑 LLM stream,SSE 流由 ReceiverStream 包 rx。
    let stop_notify = s.stop_notify(&user_id);
    let user_id_u = user.id;
    tokio::spawn(async move {
        let mut full = String::new();
        let mut usage_total: u32 = 0;
        let mut interrupted = false;

        let stream_result = backend.stream_chat(req).await;
        match stream_result {
            Ok(mut stream) => {
                loop {
                    tokio::select! {
                        // 本 pod stop 信号
                        _ = stop_notify.notified() => {
                            interrupted = true;
                            break;
                        }
                        item = stream.next() => {
                            let Some(item) = item else { break };
                            match item {
                                Ok(chunk) => {
                                    // 流式 cluster stop 轮询(写另一个 pod 的 stop_signals 时命中)。
                                    if rpg_platform::cluster::is_stop_requested(
                                        &db, user_id_u.get(), run_id,
                                    ).await {
                                        interrupted = true;
                                        break;
                                    }
                                    if let ChatChunk::Text(t) = &chunk {
                                        full.push_str(t);
                                    }
                                    if let ChatChunk::Usage(u) = &chunk {
                                        usage_total = u.output_tokens;
                                    }
                                    let wire = WireChatChunk::from_chunk(&chunk);
                                    let payload = serde_json::to_value(&wire).unwrap_or_else(|_| json!({}));
                                    if tx.send(Ok(named_sse_event("chunk", payload))).await.is_err() {
                                        return;
                                    }
                                }
                                Err(e) => {
                                    let _ = tx.send(Ok(named_sse_event(
                                        "error",
                                        json!({"detail": e.to_string(), "code": "llm_error"}),
                                    ))).await;
                                    break;
                                }
                            }
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
            }
        }

        // append assistant 文本到 history。
        if !full.is_empty() {
            let mut st = state_handle.write();
            let _ = st.append_to_path(
                "history",
                json!({"role": "assistant", "content": full.clone()}),
            );
        }
        let state_after = state_handle.read().snapshot();

        // 跨 pod stop 二次确认(被打断时 cluster::is_stop_requested 也应该为 true)。
        if !interrupted {
            interrupted = rpg_platform::cluster::is_stop_requested(&db, user_id_u.get(), run_id).await;
        }
        rpg_platform::cluster::clear_stop(&db, user_id_u.get(), run_id).await;

        // 配额回填 usage。无 Usage chunk 时 fallback est_input + 0 output。
        let out_tokens = usage_total.clamp(0, i32::MAX as u32) as i32;
        let actual = rpg_platform::usage::UsageBreakdown {
            input_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
            output_tokens: out_tokens,
            cached_input_tokens: 0,
            reasoning_tokens: 0,
            total_tokens: est_input.clamp(0, i32::MAX as i64) as i32 + out_tokens,
        };
        quota::record_actual(&db, grant, None, None, &actual, est_input as i32, 1_000_000).await;

        let _ = tx
            .send(Ok(named_sse_event(
                "done",
                json!({"status": {"state": state_after}, "interrupted": interrupted}),
            )))
            .await;
        let _ = user_id_str; // 静音 unused warning。
    });

    let stream = ReceiverStream::new(rx);
    Sse::new(stream).keep_alive(KeepAlive::default()).into_response()
}

/// chat 用的 system prompt。简化版,等接 GameMaster `build_system` 后替换。
const CHAT_SYSTEM: &str = "你是这局 TRPG 的 Game Master,根据玩家输入推进剧情。第二人称叙事,描写场景与可触发的下一步。";

/// chat 默认 max_tokens。
const CHAT_MAX_TOKENS: u32 = 800;

/// 把 [`QuotaError`] 渲染成 429 响应 + `Retry-After` 头(若有建议),
/// body 沿用 `{ok:false, detail, code}` 协议。
fn quota_error_response(user_id: &str, err: QuotaError) -> Response {
    tracing::warn!(user_id, code = err.code(), error = %err, "quota 闸拦截");
    let mut resp = (
        StatusCode::TOO_MANY_REQUESTS,
        Json(json!({
            "ok": false,
            "detail": err.to_string(),
            "code": err.code(),
        })),
    )
        .into_response();
    if let Some(secs) = err.retry_after_sec() {
        if let Ok(v) = http::HeaderValue::from_str(&secs.to_string()) {
            resp.headers_mut().insert(http::header::RETRY_AFTER, v);
        }
    }
    resp
}

/// POST /api/stop — 打断当前 chat(本 pod 快速路径 + 跨 pod DB 信号)
///
/// 两条路径并发生效:
///   1. **本 pod 快速路径**:`Notify::notify_waiters()` —— chat 在**同一 pod** 时
///      awaiter 立即短路(零延迟)。
///   2. **跨 pod 路径**:`cluster::request_stop` 往 `stop_signals` 表写一行 ——
///      chat 跑在**别的 pod** 时,那边的轮询(`is_stop_requested`)会读到并停。
///      这正是状态外置后多 pod 水平扩展的必备:进程内 Notify 命中不了别的 pod。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_stop(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    // 写状态(stop_signal)路由:强鉴权。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    // 1. 本 pod 快速路径
    if let Some(n) = s.stop_events.get(&user_id) {
        n.notify_waiters();
    }
    // 2. 跨 pod 路径:对该 user 当前 run_id 写 stop_signals(cluster 用 i64 user_id,
    //    经 UserId::get() 桥接)。没有进行中的 run(run_id 0)则跳过,避免写空信号。
    let run_id = s.current_run_id(user.id);
    if run_id != 0 {
        if let Err(e) =
            rpg_platform::cluster::request_stop(&s.db, user.id.get(), run_id).await
        {
            tracing::warn!(user_id = %user_id, run_id, error = %e, "cluster request_stop 失败");
        }
    }
    // 同步标记 state.permissions.stop_signal,便于 GM 子模块感知。
    if let Some(shared) = s.state_store.get(&user_id) {
        let mut st = shared.write();
        let _ = st.set_path("permissions.stop_signal", Value::Bool(true));
    }
    Ok(Json(json!({"ok": true})).into_response())
}

/// POST /api/save — 保存存档
///
/// 6C-1 状态外置:写 `saved_at` 戳后**调 `state_store.flush` 落库**(经注入的 saver
/// 闭包写回 `game_saves.state_snapshot`),实现跨 pod 持久化 / pod 重启不丢档。
/// 纯内存部署(无 saver 注入)时 flush 返回 false,退化为旧的"只 touch + 返回快照"。
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_save(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    // 写状态(持久化存档)路由:强鉴权。
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let (data, version) = {
        let mut st = shared.write();
        // 触一次 version+updated_at(同时让 Arc 快照缓存失效)。
        let _ = st.set_path("saved_at", Value::String(chrono::Utc::now().to_rfc3339()));
        // Arc 快照(snapshot 重建一次后返回,仅 inc refcount)。
        (st.snapshot(), st.version)
    };
    // 落库(read-through cache 的写回端)。saver 未注入(纯内存)→ false,不影响响应。
    let persisted = s.state_store.flush(&user_id).await;
    Ok(Json(json!({
        "ok": true,
        "state": data,
        "version": version,
        "persisted": persisted,
    }))
    .into_response())
}

// ── tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use futures_util::stream;
    use rpg_llm::pipeline::{
        BackendKind, ChunkStream, LlmBackend, LlmError, Usage,
    };

    /// 受控 mock backend — 用于 Wave 6-A SSE 接 LLM 流的单测。
    /// 注入一段固定 `ChatChunk` 序列,逐项 surface 出来。
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
            _req: LlmChatRequest,
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

    /// 跑一遍 mock backend,把发出来的 ChatChunk 投影成 WireChatChunk 列表。
    /// 这是 SSE handler 内部"chunk → SSE 帧"的核心转换。
    async fn drain_to_wire(backend: &MockBackend) -> Vec<WireChatChunk> {
        let req = LlmChatRequest::default();
        let mut s = backend.stream_chat(req).await.expect("stream ok");
        let mut out = Vec::new();
        while let Some(item) = s.next().await {
            if let Ok(c) = item {
                out.push(WireChatChunk::from_chunk(&c));
            }
        }
        out
    }

    /// Text + Stop → wire 帧分别带 kind=text(text=...) 与 kind=stop。
    #[tokio::test]
    async fn test_opening_text_chunk_then_stop_projects_to_wire() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Text("早上好".into())),
                Ok(ChatChunk::Stop {
                    reason: "end_turn".into(),
                }),
            ],
        };
        let wires = drain_to_wire(&backend).await;
        assert_eq!(wires.len(), 2);
        assert_eq!(wires[0].kind, "text");
        assert_eq!(wires[0].text.as_deref(), Some("早上好"));
        assert_eq!(wires[1].kind, "stop");
        assert_eq!(wires[1].stop_reason.as_deref(), Some("end_turn"));
    }

    /// Thinking + Text + Usage 三种 chunk 都能正确投影。
    #[tokio::test]
    async fn test_chat_thinking_and_usage_chunks_project_correctly() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Thinking("(深思)".into())),
                Ok(ChatChunk::Text("你看到一只猫".into())),
                Ok(ChatChunk::Usage(Usage {
                    input_tokens: 10,
                    output_tokens: 5,
                    ..Default::default()
                })),
            ],
        };
        let wires = drain_to_wire(&backend).await;
        assert_eq!(wires.len(), 3);
        assert_eq!(wires[0].kind, "thinking");
        assert_eq!(wires[1].kind, "text");
        assert_eq!(wires[2].kind, "usage");
        let u = wires[2].usage.as_ref().expect("usage payload");
        assert_eq!(u.input_tokens, 10);
        assert_eq!(u.output_tokens, 5);
    }

    /// ToolCall chunk 投影成 wire 帧带 tool_call_id / tool_name / tool_input。
    #[tokio::test]
    async fn test_chat_tool_call_chunk_projects_correctly() {
        let backend = MockBackend {
            chunks: vec![Ok(ChatChunk::ToolCall {
                id: "call_123".into(),
                name: "read_file".into(),
                input: json!({"path":"/tmp/x"}),
            })],
        };
        let wires = drain_to_wire(&backend).await;
        assert_eq!(wires.len(), 1);
        assert_eq!(wires[0].kind, "tool_call");
        assert_eq!(wires[0].tool_call_id.as_deref(), Some("call_123"));
        assert_eq!(wires[0].tool_name.as_deref(), Some("read_file"));
        assert_eq!(
            wires[0]
                .tool_input
                .as_ref()
                .and_then(|v| v.get("path"))
                .and_then(|v| v.as_str()),
            Some("/tmp/x"),
        );
    }

    /// stream_chat 抛 Err → drain 看不到 chunk(handler 应转 SSE error 帧)。
    #[tokio::test]
    async fn test_chat_backend_error_surfaces_as_error_item() {
        let backend = MockBackend {
            chunks: vec![Err(LlmError::Other("boom".into()))],
        };
        let req = LlmChatRequest::default();
        let mut stream = backend.stream_chat(req).await.expect("stream ok");
        let first = stream.next().await.expect("one item");
        assert!(first.is_err(), "first item should be Err");
    }

    /// Wave 6-A 关键约束:assistant 文本累积要拿到 ChatChunk::Text 的 text,
    /// 而不会被 Thinking / Stop 污染。
    #[tokio::test]
    async fn test_accumulated_text_skips_thinking_and_stop() {
        let backend = MockBackend {
            chunks: vec![
                Ok(ChatChunk::Thinking("(内心)".into())),
                Ok(ChatChunk::Text("Hello".into())),
                Ok(ChatChunk::Text(" world".into())),
                Ok(ChatChunk::Stop {
                    reason: "end_turn".into(),
                }),
            ],
        };
        let req = LlmChatRequest::default();
        let mut stream = backend.stream_chat(req).await.expect("stream ok");
        let mut full = String::new();
        while let Some(item) = stream.next().await {
            if let Ok(ChatChunk::Text(t)) = item {
                full.push_str(&t);
            }
        }
        assert_eq!(full, "Hello world");
    }

    /// Opening prompt 常量非空,避免硬退化掉。
    #[test]
    fn test_opening_prompts_non_empty() {
        assert!(!OPENING_SYSTEM.is_empty());
        assert!(!OPENING_USER.is_empty());
        assert!(OPENING_MAX_TOKENS > 0);
        assert!(!CHAT_SYSTEM.is_empty());
        assert!(CHAT_MAX_TOKENS > 0);
    }
}
