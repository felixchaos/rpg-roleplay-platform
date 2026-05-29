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
use http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{json, Value};

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

/// POST /api/opening — SSE 开场白流
///
/// 本翻译期未接 GameMaster.generate_opening 全链路(rpg-agents 需要 SharedLlm 注入),
/// 留下一个 stub SSE,发 hello + state_change + chunk + done,前端不会卡住。
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
    // 6C-1 Arc 快照:只读返回,snapshot() 复用缓存 Arc,避免整树深拷贝。
    let state_data = shared.read().snapshot();
    let events = vec![
        Ok::<_, Infallible>(named_sse_event("hello", hello_payload(&user_id))),
        Ok(named_sse_event(
            "state_change",
            json!({"phase":"generating","label":"GM 构思开场中…"}),
        )),
        Ok(named_sse_event("chunk", json!({"text":""}))),
        Ok(named_sse_event(
            "done",
            json!({"status": {"state": state_data}, "interrupted": false}),
        )),
    ];
    let stream = stream::iter(events);
    Ok(Sse::new(stream).keep_alive(KeepAlive::default()))
}

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

/// POST /api/chat — 主聊天 SSE
///
/// Python 端是 5 阶段 chat_pipeline。Rust 翻译期 GameMaster 链路 (rpg-agents + rpg-llm
/// router 注入) 还在搭,本接口只做空消息校验 + 把 user input 写进 history + echo 一个空 token,
/// 等 chat_pipeline crate 落地后再 wire 真实流式响应。
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
    // 6C-1 Arc 快照(写后重建一次,SSE done 返回最新 state 仅 inc refcount)。
    let state_after = shared.read().snapshot();

    // ── 调用 LLM(本翻译期为 stub,无真实 output)。链路落地后此处填真实 usage。
    // 调用后回填用量 + 释放并发槽。stub 阶段 actual 用预估输入 + 0 output。
    let actual = rpg_platform::usage::UsageBreakdown {
        input_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
        output_tokens: 0,
        cached_input_tokens: 0,
        reasoning_tokens: 0,
        total_tokens: est_input.clamp(0, i32::MAX as i64) as i32,
    };
    quota::record_actual(&s.db, grant, None, None, &actual, est_input as i32, 1_000_000).await;

    // 6C-1 跨 pod stop 检查点(真实 chat_pipeline 落地后应在流式循环里周期轮询):
    // 本 pod 的 Notify 给真实 await 循环用(stub 无循环故不读它);此处查
    // cluster::is_stop_requested —— 命中**别的 pod** 写入 stop_signals 也能感知。
    let interrupted =
        rpg_platform::cluster::is_stop_requested(&s.db, user.id.get(), run_id).await;
    // 本轮结束:清掉跨进程 stop 信号(避免孤儿信号污染下一轮)。
    rpg_platform::cluster::clear_stop(&s.db, user.id.get(), run_id).await;

    let events = vec![
        Ok::<_, Infallible>(named_sse_event("hello", hello_payload(&user_id))),
        Ok(named_sse_event(
            "state_change",
            json!({"phase":"generating","label":"GM 思考中…"}),
        )),
        Ok(named_sse_event("chunk", json!({"text":""}))),
        Ok(named_sse_event(
            "done",
            json!({"status": {"state": state_after}, "interrupted": interrupted}),
        )),
    ];
    Sse::new(stream::iter(events))
        .keep_alive(KeepAlive::default())
        .into_response()
}

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
