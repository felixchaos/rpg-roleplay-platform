//! core.rs — 入口 + 状态路由
//! GET  /                 — backend 根路径
//! GET  /api/state        — 当前游戏状态快照
//! GET  /api/state_events — state-change SSE 通道

use std::convert::Infallible;
use std::time::Duration;

use axum::{
    extract::State,
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::get,
    Json, Router,
};
use futures_util::stream::{self, Stream};
use http::HeaderMap;
use serde_json::json;

use crate::{hello_payload, named_sse_event, require_user, user_id_or_anon, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(index))
        .route("/api/state", get(api_state))
        .route("/api/state_events", get(api_state_events))
}

/// GET / — backend 根路径
async fn index() -> impl IntoResponse {
    Json(json!({
        "ok": true,
        "service": format!("{} RPG backend (Rust/Axum)", rpg_core::config::app_title()),
        "docs": "/docs",
    }))
}

/// GET /api/state — 当前游戏状态快照
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_state(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let shared = s.state_store.get_or_create(&user_id).await;
    let snapshot = shared.read().clone();
    Ok(Json(json!({
        "ok": true,
        "state": snapshot.data,
        "version": snapshot.version,
        "updated_at": snapshot.updated_at,
        "user_id": snapshot.user_id,
    }))
    .into_response())
}

/// GET /api/state_events — 长连 SSE,推送 state 变更事件
///
/// # W3-2 TODO
/// `rpg_state::bus::subscribe()` 尚未实装(W3-2 并行任务)。
/// 当前实现发送 hello 帧 + keepalive,等 W3-2 合并后:
///   1. 在 `AppState` 加 `state_bus: tokio::sync::broadcast::Sender<StateEvent>`
///   2. 将 `state_bus.subscribe()` 转换成 `ReceiverStream`
///   3. 把每个 `StateEvent` 序列化为 SSE `data`
#[tracing::instrument(skip(s, headers), fields(user_id))]
async fn api_state_events(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let user_id_str = user.id.to_string();
    // 首条 hello — 前端用此 reset backoff。
    let hello = named_sse_event("hello", hello_payload(&user_id_str));
    // TODO(W3-2): replace with real bus subscription:
    //   let rx = s.state_bus.subscribe();
    //   let bus_stream = BroadcastStream::new(rx).filter_map(|r| ...);
    //   let stream = stream::once(async { Ok(hello) }).chain(bus_stream);
    let stream = stream::iter(vec![Ok::<_, Infallible>(hello)]);
    Ok(Sse::new(stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(25))
            .text("keepalive"),
    ))
}
