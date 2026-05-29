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
use futures_util::stream::{self, Stream};
use http::HeaderMap;
use serde::Deserialize;
use serde_json::{json, Value};

use rpg_state::GameState;

use crate::{user_id_or_anon, AppState, ResponseError};

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
async fn api_new(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<NewGameRequest>>,
) -> Result<Response, ResponseError> {
    let body = body.map(|Json(b)| b).unwrap_or_default();
    let user_id = user_id_or_anon(&s, &headers).await;
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
    let snapshot = shared.read().clone();
    Ok(Json(json!({
        "ok": true,
        "state": snapshot.data,
        "version": snapshot.version,
    }))
    .into_response())
}

/// POST /api/opening — SSE 开场白流
///
/// 本翻译期未接 GameMaster.generate_opening 全链路(rpg-agents 需要 SharedLlm 注入),
/// 留下一个 stub SSE,发 stage + done,前端不会卡住。
async fn api_opening(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    let events = vec![
        Ok::<_, Infallible>(
            Event::default()
                .event("stage")
                .data(json!({"phase":"generating","label":"GM 构思开场中…"}).to_string()),
        ),
        Ok(Event::default()
            .event("token")
            .data(json!({"text":""}).to_string())),
        Ok(Event::default()
            .event("done")
            .data(json!({"status": {"state": snapshot.data}, "interrupted": false}).to_string())),
    ];
    let stream = stream::iter(events);
    Ok(Sse::new(stream).keep_alive(KeepAlive::default()))
}

/// POST /api/chat/estimate — 实时上下文预估
///
/// 本翻译期没接 platform_app.usage,给一个极轻量估算:
/// 输入 = system(1200) + history_char/3 + retrieval(800 if include) + message_char/3。
async fn api_chat_estimate(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ChatEstimateRequest>,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    let message = body.message.unwrap_or_default();
    let include_retrieval = body.include_retrieval.unwrap_or(true);
    let history_text = snapshot
        .data
        .get("history")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|m| m.get("content").and_then(|v| v.as_str()))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();
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
async fn api_context_breakdown(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    let last_ctx = snapshot
        .data
        .get("memory")
        .and_then(|m| m.get("last_context"))
        .cloned()
        .unwrap_or(json!({}));
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

/// POST /api/chat — 主聊天 SSE
///
/// Python 端是 5 阶段 chat_pipeline。Rust 翻译期 GameMaster 链路 (rpg-agents + rpg-llm
/// router 注入) 还在搭,本接口只做空消息校验 + 把 user input 写进 history + echo 一个空 token,
/// 等 chat_pipeline crate 落地后再 wire 真实流式响应。
async fn api_chat(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Option<Json<ChatRequest>>,
) -> Response {
    let body = body.map(|Json(b)| b).unwrap_or_default();
    let user_id = user_id_or_anon(&s, &headers).await;
    let message = body
        .message
        .or(body.text)
        .unwrap_or_default()
        .trim()
        .to_string();
    if message.is_empty() {
        let evt = Event::default()
            .event("error")
            .data(json!({"message":"空消息"}).to_string());
        let stream = stream::iter(vec![Ok::<_, Infallible>(evt)]);
        return Sse::new(stream).into_response();
    }
    // 清零该 user 的 stop notify(重新建一个,旧的 awaiter 自动 drop)。
    s.stop_events.remove(&user_id);
    let _stop = s.stop_notify(&user_id);

    let shared = s.state_store.get_or_create(&user_id).await;
    // 写入 user 消息到 history
    {
        let mut st = shared.write();
        let _ = st.append_to_path(
            "history",
            json!({"role": "user", "content": message.clone()}),
        );
    }
    let snapshot_after = shared.read().clone();
    let events = vec![
        Ok::<_, Infallible>(
            Event::default()
                .event("stage")
                .data(json!({"phase":"generating","label":"GM 思考中…"}).to_string()),
        ),
        Ok(Event::default()
            .event("token")
            .data(json!({"text":""}).to_string())),
        Ok(Event::default().event("done").data(
            json!({"status": {"state": snapshot_after.data}, "interrupted": false}).to_string(),
        )),
    ];
    Sse::new(stream::iter(events))
        .keep_alive(KeepAlive::default())
        .into_response()
}

/// POST /api/stop — 打断当前 chat
///
/// 拿到当前 user 的 Notify,notify_waiters。chat 流里 awaiter 收到后能短路。
async fn api_stop(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    if let Some(n) = s.stop_events.get(&user_id) {
        n.notify_waiters();
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
/// Python 端调 dispatcher save_runtime → rpg-platform runtime backend。
/// 本翻译期暂用 state.touch() + 返回最新 snapshot;真实持久化等 dispatcher 落地后接。
async fn api_save(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = {
        let mut st = shared.write();
        // 触一次 version+updated_at,模拟"保存"。
        let _ = st.set_path("saved_at", Value::String(chrono::Utc::now().to_rfc3339()));
        st.clone()
    };
    Ok(Json(json!({
        "ok": true,
        "state": snapshot.data,
        "version": snapshot.version,
    }))
    .into_response())
}
