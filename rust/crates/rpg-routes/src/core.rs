//! core.rs — 入口 + 状态路由
//! GET  /                 — backend 根路径
//! GET  /api/state        — 当前游戏状态快照
//! GET  /api/state_events — state-change SSE 通道(订阅 [`rpg_state::StateEventBus`])

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
use futures_util::stream::{Stream, StreamExt};
use http::HeaderMap;
use rpg_state::StateEvent;
use serde_json::json;
use tokio_stream::wrappers::{errors::BroadcastStreamRecvError, BroadcastStream};

use crate::sse_events::SseStateBusPayload;
use crate::{hello_payload, named_sse_event, require_user, user_id_or_anon, AppState, ResponseError};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(index))
        .route("/api/state", get(api_state))
        .route("/api/state_events", get(api_state_events))
}

/// 非 SSE 路由(供 build_regular_routes 使用)。
pub fn regular_router() -> Router<AppState> {
    Router::new()
        .route("/", get(index))
        .route("/api/state", get(api_state))
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

/// GET /api/state_events — 长连 SSE,推送 state 变更事件(对应 Python `task 69`)。
///
/// 协议:
/// - 首帧 `hello` — 前端用于 reset backoff(`state-event-bridge.js`)
/// - 后续 `state_change` — 仅推送当前 user_id 的 [`StateEvent`],wire 形态
///   `{ topic, op, user_id, payload, ts }`(与 Python `state_event_bus.to_sse_data`
///   兼容)。
/// - keepalive — 25s 注释帧(axum `KeepAlive`)
///
/// W3-2:落实 [`rpg_state::StateEventBus`] 订阅。`state_store` 自带 bus,
/// `apply_op` 成功后 publish;此处 `subscribe()` 拿独立 receiver。
/// 慢消费者:broadcast 容量 256,落后会拿到 `Lagged(n)` — 我们直接跳过 lag 帧,
/// 前端 watchdog(45s 无事件)会强制重连重新拉快照。多 tab 同账号
/// 各开一个连接,bus 内 fan-out 复用同一 publisher。
#[tracing::instrument(skip(s, headers), fields(user_id))]
pub(crate) async fn api_state_events(
    State(s): State<AppState>,
    headers: HeaderMap,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ResponseError> {
    let user = require_user(&s, &headers).await?;
    tracing::Span::current().record("user_id", tracing::field::display(&user.id));
    let user_id_str = user.id.to_string();

    // 首条 hello — 前端用此 reset backoff。
    let hello = named_sse_event("hello", hello_payload(&user_id_str));
    let hello_stream = futures_util::stream::once(async move { Ok::<_, Infallible>(hello) });

    // bus 订阅:state_store.bus().subscribe() 返回 broadcast::Receiver<StateEvent>,
    // 用 BroadcastStream 适配成 futures::Stream;过滤 user_id 不匹配的事件,
    // lag 错误打点后丢帧(由 watchdog 兜底)。
    let rx = s.state_store.subscribe();
    let user_filter = user_id_str.clone();
    let bus_stream = BroadcastStream::new(rx).filter_map(move |item| {
        let user_filter = user_filter.clone();
        async move {
            match item {
                Ok(event) => {
                    if event.user_id() != user_filter {
                        return None;
                    }
                    let payload = state_event_to_wire(&event, &user_filter);
                    let sse = named_sse_event("state_change", serde_json::to_value(&payload).ok()?);
                    Some(Ok::<_, Infallible>(sse))
                }
                Err(BroadcastStreamRecvError::Lagged(n)) => {
                    tracing::warn!(lagged = n, "state_events bus lagged, dropping frames");
                    None
                }
            }
        }
    });

    let stream = hello_stream.chain(bus_stream);

    Ok(Sse::new(stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(25))
            .text("keepalive"),
    ))
}

/// 把 [`StateEvent`] 投影到前端 wire 形态 `{ topic, op, user_id, payload, ts }`。
///
/// 与 Python `state_event_bus.StateEvent.to_sse_data` 对齐 — 前端
/// `state-event-bridge.js` 据 `topic` 派 `rpg-{topic}-updated` CustomEvent,
/// 各页面已有 listener 自动 refetch。
fn state_event_to_wire(event: &StateEvent, user_id: &str) -> SseStateBusPayload {
    let ts = chrono::Utc::now().timestamp();
    let (topic, op, payload) = match event {
        StateEvent::Updated { version, .. } => (
            "state".to_string(),
            "updated".to_string(),
            json!({ "version": version }),
        ),
        StateEvent::OpApplied {
            version,
            op,
            source,
            ..
        } => (
            "state".to_string(),
            "applied".to_string(),
            json!({ "version": version, "op": op, "source": source }),
        ),
        StateEvent::PendingAdded {
            pending_id,
            path,
            source,
            ..
        } => (
            "pending".to_string(),
            "added".to_string(),
            json!({ "pending_id": pending_id, "path": path, "source": source }),
        ),
        StateEvent::PendingResolved {
            pending_id,
            approved,
            path,
            ..
        } => (
            "pending".to_string(),
            "resolved".to_string(),
            json!({ "pending_id": pending_id, "approved": approved, "path": path }),
        ),
        StateEvent::QuestionAdded {
            question_id,
            question,
            source,
            ..
        } => (
            "questions".to_string(),
            "added".to_string(),
            json!({ "question_id": question_id, "question": question, "source": source }),
        ),
        StateEvent::QuestionAnswered {
            question_id,
            choice,
            ..
        } => (
            "questions".to_string(),
            "answered".to_string(),
            json!({ "question_id": question_id, "choice": choice }),
        ),
        StateEvent::TimelineJump {
            anchor_state,
            world_time,
            ..
        } => (
            "timeline".to_string(),
            "jump".to_string(),
            json!({ "anchor_state": anchor_state, "world_time": world_time }),
        ),
        StateEvent::WorldlineValidation {
            status, message, ..
        } => (
            "worldline".to_string(),
            "validated".to_string(),
            json!({ "status": status, "message": message }),
        ),
        StateEvent::Custom {
            event_type,
            payload,
            ..
        } => (
            event_type.clone(),
            "custom".to_string(),
            payload.clone(),
        ),
    };
    SseStateBusPayload {
        topic,
        op,
        user_id: user_id.to_string(),
        payload,
        ts,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rpg_state::ops::Op;

    fn user_id_string(s: &str) -> String {
        s.to_string()
    }

    #[test]
    fn updated_event_maps_to_state_updated() {
        let ev = StateEvent::Updated {
            user_id: user_id_string("42"),
            version: 7,
        };
        let wire = state_event_to_wire(&ev, "42");
        assert_eq!(wire.topic, "state");
        assert_eq!(wire.op, "updated");
        assert_eq!(wire.user_id, "42");
        assert_eq!(wire.payload["version"], 7);
    }

    #[test]
    fn pending_added_maps_to_pending_added() {
        let ev = StateEvent::PendingAdded {
            user_id: user_id_string("9"),
            pending_id: "p1".into(),
            path: "/a/b".into(),
            source: "tool".into(),
        };
        let wire = state_event_to_wire(&ev, "9");
        assert_eq!(wire.topic, "pending");
        assert_eq!(wire.op, "added");
        assert_eq!(wire.payload["pending_id"], "p1");
        assert_eq!(wire.payload["path"], "/a/b");
        assert_eq!(wire.payload["source"], "tool");
    }

    #[test]
    fn question_answered_passes_choice() {
        let ev = StateEvent::QuestionAnswered {
            user_id: user_id_string("1"),
            question_id: "q1".into(),
            choice: Some("yes".into()),
        };
        let wire = state_event_to_wire(&ev, "1");
        assert_eq!(wire.topic, "questions");
        assert_eq!(wire.op, "answered");
        assert_eq!(wire.payload["choice"], "yes");
    }

    #[test]
    fn custom_event_uses_event_type_as_topic() {
        let ev = StateEvent::Custom {
            user_id: user_id_string("1"),
            event_type: "saves".into(),
            payload: json!({ "name": "slot1" }),
        };
        let wire = state_event_to_wire(&ev, "1");
        assert_eq!(wire.topic, "saves");
        assert_eq!(wire.op, "custom");
        assert_eq!(wire.payload["name"], "slot1");
    }

    #[tokio::test]
    async fn bus_subscribe_filters_by_user_id() {
        // 模拟两个 user 共享同一 bus —— subscriber 只应收到 user_id 匹配的事件。
        let store = rpg_state::StateStore::new();
        let mut rx = store.subscribe();
        store.bus().publish(StateEvent::Updated {
            user_id: "other".into(),
            version: 1,
        });
        store.bus().publish(StateEvent::Updated {
            user_id: "me".into(),
            version: 2,
        });

        let mut me_events = Vec::new();
        // 拉两条
        for _ in 0..2 {
            let ev = rx.recv().await.unwrap();
            if ev.user_id() == "me" {
                me_events.push(state_event_to_wire(&ev, "me"));
            }
        }
        assert_eq!(me_events.len(), 1);
        assert_eq!(me_events[0].topic, "state");
        assert_eq!(me_events[0].payload["version"], 2);
    }

    #[tokio::test]
    async fn bus_publish_round_trips_through_wire() {
        // 直接通过 bus.publish 推一条 OpApplied,验证订阅方收到后投影成
        // 前端期望的 wire 形态。不走 apply_op 是为了避免触碰路径校验/权限,
        // 本测点只检查 bus → wire 这一段。
        let store = rpg_state::StateStore::new();
        let mut rx = store.subscribe();
        let op = Op::Set {
            path: "any.thing".into(),
            value: json!("v"),
        };
        store.bus().publish(StateEvent::OpApplied {
            user_id: "u1".into(),
            version: 3,
            op,
            source: "test".into(),
        });
        store.bus().publish(StateEvent::Updated {
            user_id: "u1".into(),
            version: 3,
        });

        let mut saw_op_applied = false;
        let mut saw_updated = false;
        while let Ok(ev) = rx.try_recv() {
            match &ev {
                StateEvent::OpApplied { user_id, .. } => {
                    assert_eq!(user_id, "u1");
                    let wire = state_event_to_wire(&ev, "u1");
                    assert_eq!(wire.topic, "state");
                    assert_eq!(wire.op, "applied");
                    assert_eq!(wire.payload["version"], 3);
                    assert_eq!(wire.payload["source"], "test");
                    saw_op_applied = true;
                }
                StateEvent::Updated { user_id, .. } => {
                    assert_eq!(user_id, "u1");
                    let wire = state_event_to_wire(&ev, "u1");
                    assert_eq!(wire.topic, "state");
                    assert_eq!(wire.op, "updated");
                    saw_updated = true;
                }
                _ => {}
            }
        }
        assert!(saw_op_applied, "expected OpApplied event");
        assert!(saw_updated, "expected Updated event");
    }
}
