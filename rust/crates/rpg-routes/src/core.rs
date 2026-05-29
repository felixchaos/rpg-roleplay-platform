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

use crate::{require_user, user_id_or_anon, AppState, ResponseError};

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
async fn api_state(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ResponseError> {
    let user_id = user_id_or_anon(&s, &headers).await;
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
/// 本翻译期没接 Python 的 `state_event_bus`,只发 hello + keepalive,
/// 等 rpg-state 端补 event bus 后再 wire 进真实订阅。
async fn api_state_events(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let hello = Event::default()
        .event("hello")
        .data(json!({ "user_id": user.id, "ts": chrono::Utc::now().timestamp() }).to_string());
    let stream = stream::iter(vec![Ok::<_, Infallible>(hello)]);
    Ok(Sse::new(stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(25))
            .text("keepalive"),
    ))
}
