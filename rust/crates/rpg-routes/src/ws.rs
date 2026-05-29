//! ws.rs — WebSocket 双向 bus(Wave 10-B)。
//!
//! 现状:`/api/state_events` 是 SSE,单向 server → client。前端若要发实时
//! control 事件(typing indicator / 中途打断 / 主动续期心跳),只能轮询 REST。
//! 本模块挂 `/api/ws`,基于 axum 内置 `WebSocketUpgrade`(底层 tungstenite),
//! 复用同一份 [`rpg_state::StateEventBus`] 把状态变更投递给已连接客户端,
//! 同时接受 client → server 的 typed 控制消息。
//!
//! 协议:
//! - **subprotocol**:不做 negotiation,默认 JSON 文本帧。
//! - **server → client** envelope([`WsServerMessage`]):
//!   `hello` / `state_change` / `chunk` / `done` / `error` / `pong` / `typing`。
//!   `state_change` 与 SSE [`crate::sse_events::SseStateBusPayload`] 同字段,
//!   方便前端两边复用同一份 dispatch 代码。
//! - **client → server** envelope([`WsClientMessage`]):
//!   `ping` / `stop` / `typing(bool)` / `subscribe(topics)`。
//!   - `stop`:写 `stop_signals` 表(V018),触发跨 pod 取消(等价 `/api/stop`)。
//!   - `typing`:广播 [`StateEvent::Custom`] 到 SSE bus,其它 tab / 其它端
//!     就能从 `/api/state_events` 或本 ws 看到 typing 事件(同账号下)。
//!   - `subscribe`:目前后端不做 topic 过滤(永远全推),保留协议字段;
//!     当 ws 路径接更多业务总线时按 topic 路由。
//! - **心跳**:server 每 30s 主动发 `WsServerMessage::Pong { ts }`;若 60s
//!   内没收到任何 client frame(含 application Ping、Pong 控帧、文本帧),
//!   server close。注:tungstenite 也会做 ws-level Pong 自动回包,所以
//!   client 不主动应答 application pong 也不会被掐 — 只要还在传任何帧即可。
//!
//! 路径:`/api/ws`(经全局 rewrite middleware,`/api/v1/ws` 也可用)。
//!
//! 与 SSE 的关系:`/api/state_events` 保留不动,两者并存 — 旧 client 继续
//! SSE,新 client 走 ws。两路同时订阅同一 bus,广播 fan-out。
//!
//! 单测:不开真实 socket,直接构造 [`ws_loop_inner`](泛型 Sink/Stream),
//! 模拟 ping/stop/typing/state_change 等场景。HTTP 层 handshake 由 axum
//! 自身覆盖(我们不重新发明)。

use std::time::Duration;

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    response::IntoResponse,
    routing::get,
    Router,
};
use futures_util::{
    sink::SinkExt,
    stream::StreamExt,
    Sink, Stream,
};
use http::HeaderMap;
use rpg_state::StateEvent;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::broadcast::error::RecvError;
use tokio::time::interval;

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

use crate::sse_events::SseStateBusPayload;
use crate::{hello_payload, require_user, AppState, ResponseError};

/// 服务端 → 客户端消息。tagged union(`type` 字段)便于前端 discriminated union。
///
/// 与 SSE event 名对齐:`hello` / `state_change` / `chunk` / `done` / `error`,
/// 再加 ws 特有的 `pong`(响应 client ping)+ `typing`(广播 typing 状态)。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WsServerMessage {
    /// 握手首帧。
    Hello { payload: WsHelloPayload },
    /// 状态变更(state-event bus 投影)。
    StateChange { payload: SseStateBusPayload },
    /// 流式 chunk(后续 chat-over-ws 拓展用,目前 SSE 仍走 /api/chat)。
    Chunk { payload: Value },
    /// 流末。
    Done { payload: Value },
    /// 错误。
    Error { payload: WsErrorPayload },
    /// server-driven ping(以 pong 命名是因为它是对 client ping 的响应,
    /// 也兼做主动心跳)。
    Pong { ts: i64 },
    /// 同账号下 typing 状态广播(其它 tab / 其它端能看到)。
    Typing { user_id: String, typing: bool, ts: i64 },
}

/// 客户端 → 服务端消息。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WsClientMessage {
    /// 应用层 ping(server 会回 pong)。
    Ping {
        #[serde(default)]
        ts: Option<i64>,
    },
    /// 主动取消当前 chat(等价 POST /api/stop)。
    Stop,
    /// 通知 server 自己正在 typing(true)/ 停止 typing(false)。
    Typing { typing: bool },
    /// 订阅 topic 列表(目前服务端不做过滤,保留字段)。
    Subscribe { topics: Vec<String> },
}

/// `hello` payload — 复用 SSE 形态,加 protocol="ws-v1"。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct WsHelloPayload {
    pub user_id: String,
    pub ts: i64,
    pub protocol: String,
}

/// `error` payload。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct WsErrorPayload {
    pub detail: String,
    pub code: String,
}

/// 心跳间隔。server 每隔此周期主动发 Pong 帧。
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(30);
/// 没收到任何 client 帧(含 Pong / Ping / Text / Binary)超过此时长,close。
/// 设为 2× 心跳间隔留 jitter buffer。
const CLIENT_IDLE_TIMEOUT: Duration = Duration::from_secs(60);

pub fn router() -> Router<AppState> {
    Router::new().route("/api/ws", get(api_ws))
}

/// `GET /api/ws` handler:升级到 WebSocket,然后跑 [`ws_loop`]。
///
/// 鉴权同其他 SSE 路由:cookie / Authorization header。失败 → 401(`ResponseError`)。
/// 升级成功后,业务在 [`ws_loop`] 里跑。
#[tracing::instrument(skip(s, ws, headers), fields(user_id))]
pub async fn api_ws(
    State(s): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Result<axum::response::Response, ResponseError> {
    let user = require_user(&s, &headers).await?;
    let user_id = user.id.to_string();
    tracing::Span::current().record("user_id", tracing::field::display(&user_id));
    let user_id_for_loop = user_id.clone();
    Ok(ws.on_upgrade(move |socket| ws_loop(socket, s, user_id_for_loop)))
        .map(|r| r.into_response())
}

/// ws 主循环 — 拆分 socket 后跑泛型 [`ws_loop_inner`]。
async fn ws_loop(socket: WebSocket, state: AppState, user_id: String) {
    let (sink, stream) = socket.split();
    if let Err(err) = ws_loop_inner(sink, stream, state, user_id).await {
        tracing::warn!(error = %err, "ws_loop_inner exited with error");
    }
}

/// 错误类型 — ws_loop_inner 用,只内部流转。
#[derive(Debug)]
pub(crate) enum WsLoopError {
    Send,
    SocketClosed,
}

impl std::fmt::Display for WsLoopError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WsLoopError::Send => write!(f, "failed to send frame"),
            WsLoopError::SocketClosed => write!(f, "socket closed"),
        }
    }
}

/// 泛型主循环 — 把 sink / stream 抽出来便于单测(可塞 mpsc channel 模拟)。
///
/// 设计:
/// 1. 启动时发 `hello`。
/// 2. `tokio::select!` 多路:
///    - **bus**:订阅 [`rpg_state::StateEventBus`],收到 `StateEvent` 投影为
///      [`WsServerMessage::StateChange`] 发给客户端。仅匹配 `user_id`。
///    - **client frame**:读 socket,parse 成 [`WsClientMessage`],按类型派工
///      (`Ping`→`Pong`、`Stop`→ `stop_signals`、`Typing`→ broadcast bus 自定义事件、
///      `Subscribe`→ noop)。Pong / Ping 控帧也算 client 活动,刷新 idle timer。
///    - **heartbeat tick**:每 30s 发 `Pong { ts }`。
///    - **idle timeout**:60s 无 client 帧 → close。
/// 3. 任一 send 失败 / client close / idle timeout → 退出。
pub(crate) async fn ws_loop_inner<S, R>(
    mut sink: S,
    mut stream: R,
    state: AppState,
    user_id: String,
) -> Result<(), WsLoopError>
where
    S: Sink<Message> + Unpin,
    R: Stream<Item = Result<Message, axum::Error>> + Unpin,
{
    // 1. hello
    let hello = WsServerMessage::Hello {
        payload: serde_json::from_value::<WsHelloPayload>(hello_payload(&user_id))
            .unwrap_or(WsHelloPayload {
                user_id: user_id.clone(),
                ts: chrono::Utc::now().timestamp(),
                protocol: "ws-v1".into(),
            }),
    };
    send_typed(&mut sink, &hello).await?;

    // bus 订阅
    let mut bus_rx = state.state_store.subscribe();

    // heartbeat
    let mut heartbeat = interval(HEARTBEAT_INTERVAL);
    heartbeat.tick().await; // immediate tick consumed

    // idle timeout — 用 reset 模式;每次收到 client 帧 reset_at 推到 now()。
    let mut idle_deadline = tokio::time::Instant::now() + CLIENT_IDLE_TIMEOUT;

    loop {
        let idle_sleep = tokio::time::sleep_until(idle_deadline);
        tokio::pin!(idle_sleep);
        tokio::select! {
            // bus 事件
            bus_event = bus_rx.recv() => {
                match bus_event {
                    Ok(event) => {
                        if event.user_id() != user_id {
                            continue;
                        }
                        let wire = state_event_to_wire(&event, &user_id);
                        let msg = WsServerMessage::StateChange { payload: wire };
                        send_typed(&mut sink, &msg).await?;
                    }
                    Err(RecvError::Lagged(n)) => {
                        tracing::warn!(lagged = n, "ws bus lagged, dropping frames");
                    }
                    Err(RecvError::Closed) => {
                        // bus 关闭(进程退出),正常 close。
                        return Ok(());
                    }
                }
            }
            // client frame
            client_frame = stream.next() => {
                idle_deadline = tokio::time::Instant::now() + CLIENT_IDLE_TIMEOUT;
                match client_frame {
                    Some(Ok(Message::Text(text))) => {
                        match serde_json::from_str::<WsClientMessage>(&text) {
                            Ok(msg) => handle_client_message(msg, &state, &user_id, &mut sink).await?,
                            Err(e) => {
                                let err = WsServerMessage::Error {
                                    payload: WsErrorPayload {
                                        detail: format!("invalid client message: {e}"),
                                        code: crate::error_codes::BAD_REQUEST.to_string(),
                                    },
                                };
                                send_typed(&mut sink, &err).await?;
                            }
                        }
                    }
                    Some(Ok(Message::Binary(_))) => {
                        // 不支持二进制,忽略(不算错误,只是协议外)。
                    }
                    Some(Ok(Message::Ping(payload))) => {
                        // ws-level Ping → 回 Pong 控帧。
                        if sink.send(Message::Pong(payload)).await.is_err() {
                            return Err(WsLoopError::Send);
                        }
                    }
                    Some(Ok(Message::Pong(_))) => {
                        // 已 reset idle,忽略 payload。
                    }
                    Some(Ok(Message::Close(_))) | None => {
                        return Err(WsLoopError::SocketClosed);
                    }
                    Some(Err(e)) => {
                        tracing::debug!(error = %e, "ws read error, closing");
                        return Err(WsLoopError::SocketClosed);
                    }
                }
            }
            // heartbeat
            _ = heartbeat.tick() => {
                let pong = WsServerMessage::Pong { ts: chrono::Utc::now().timestamp() };
                send_typed(&mut sink, &pong).await?;
            }
            // idle timeout
            _ = &mut idle_sleep => {
                tracing::debug!(user_id = %user_id, "ws idle timeout, closing");
                let _ = sink.send(Message::Close(None)).await;
                return Ok(());
            }
        }
    }
}

/// 处理一条 client → server 消息。
async fn handle_client_message<S>(
    msg: WsClientMessage,
    state: &AppState,
    user_id: &str,
    sink: &mut S,
) -> Result<(), WsLoopError>
where
    S: Sink<Message> + Unpin,
{
    match msg {
        WsClientMessage::Ping { .. } => {
            let pong = WsServerMessage::Pong { ts: chrono::Utc::now().timestamp() };
            send_typed(sink, &pong).await?;
        }
        WsClientMessage::Stop => {
            // 1. 本 pod 快速路径
            if let Some(n) = state.stop_events.get(user_id) {
                n.notify_waiters();
            }
            // 2. 跨 pod stop_signals — 需要 i64 user_id。user_id 字串可能不是数字
            //    (匿名 / "anonymous"),解析失败就跳过(等价没有 run)。
            if let Ok(user_i64) = user_id.parse::<i64>() {
                // run_id 从 run_ids 表里取;字符串 user_id 没法直接索引 UserId,
                // 但 run_ids 的 key 是 UserId — 这里只 best-effort 走 i64 桥接。
                let run_id = state
                    .run_ids
                    .iter()
                    .find(|kv| kv.key().get() == user_i64)
                    .map(|kv| *kv.value() as i64)
                    .unwrap_or(0);
                if run_id != 0 {
                    if let Err(e) =
                        rpg_platform::cluster::request_stop(&state.db, user_i64, run_id).await
                    {
                        tracing::warn!(user_id = %user_id, run_id, error = %e, "ws stop: cluster request_stop 失败");
                    }
                }
            }
            // 同步 state.permissions.stop_signal,与 /api/stop 行为一致。
            if let Some(shared) = state.state_store.get(user_id) {
                let mut st = shared.write();
                let _ = st.set_path("permissions.stop_signal", Value::Bool(true));
            }
        }
        WsClientMessage::Typing { typing } => {
            // 广播 typing 到 bus,其它订阅(SSE / ws)能收到。
            let event = StateEvent::Custom {
                user_id: user_id.to_string(),
                event_type: "typing".into(),
                payload: json!({ "typing": typing }),
            };
            state.state_store.bus().publish(event);
        }
        WsClientMessage::Subscribe { topics } => {
            // 后端不做过滤,只埋点 debug 用。
            tracing::debug!(user_id = %user_id, topics = ?topics, "ws subscribe (noop)");
        }
    }
    Ok(())
}

/// 把 typed WsServerMessage 序列化成 JSON 文本帧发出。
async fn send_typed<S>(sink: &mut S, msg: &WsServerMessage) -> Result<(), WsLoopError>
where
    S: Sink<Message> + Unpin,
{
    let text = match serde_json::to_string(msg) {
        Ok(t) => t,
        Err(e) => {
            tracing::error!(error = %e, "ws message serialize failed");
            return Err(WsLoopError::Send);
        }
    };
    sink.send(Message::Text(text))
        .await
        .map_err(|_| WsLoopError::Send)
}

/// 把 [`StateEvent`] 投影到 wire bus payload — 与 core.rs 同形,但写在这里
/// 避免循环依赖,后续可以抽公共 helper。
fn state_event_to_wire(event: &StateEvent, user_id: &str) -> SseStateBusPayload {
    let ts = chrono::Utc::now().timestamp();
    let (topic, op, payload) = match event {
        StateEvent::Updated { version, .. } => (
            "state".to_string(),
            "updated".to_string(),
            json!({ "version": version }),
        ),
        StateEvent::OpApplied {
            version, op, source, ..
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
        } => (event_type.clone(), "custom".to_string(), payload.clone()),
    };
    SseStateBusPayload {
        topic,
        op,
        user_id: user_id.to_string(),
        payload,
        ts,
    }
}

// ── tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::stream;
    use sqlx::postgres::PgPoolOptions;
    use std::pin::Pin;
    use std::task::{Context, Poll};
    use tokio::sync::mpsc;

    /// 构造一个本地内存 AppState — 不连真实 DB,用 lazy pool。
    /// 给只走 state_store / stop_events / bus 的测试用。
    fn fake_state() -> AppState {
        // sqlx 的 lazy pool 允许在没有真实连接的情况下持有 pool 句柄。
        // 测试里我们不会去用它(typing/ping/subscribe 不访问 DB)。
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect_lazy("postgres://localhost/disabled")
            .expect("lazy pool");
        AppState::new(pool)
    }

    /// 直接实现 Sink 的小适配器:把 Message 推到 unbounded mpsc。
    /// 不依赖 futures::sink::unfold(它返回的 Sink 不 Unpin)。
    struct TxSink(mpsc::UnboundedSender<Message>);

    impl Sink<Message> for TxSink {
        type Error = ();
        fn poll_ready(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
            Poll::Ready(Ok(()))
        }
        fn start_send(self: Pin<&mut Self>, item: Message) -> Result<(), Self::Error> {
            self.0.send(item).map_err(|_| ())
        }
        fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
            Poll::Ready(Ok(()))
        }
        fn poll_close(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
            Poll::Ready(Ok(()))
        }
    }

    fn sink_from_tx(tx: mpsc::UnboundedSender<Message>) -> TxSink {
        TxSink(tx)
    }

    /// 把 Vec<Message> 包成 Stream<Item = Result<Message, axum::Error>>。
    fn stream_from_vec(
        msgs: Vec<Message>,
    ) -> impl Stream<Item = Result<Message, axum::Error>> + Unpin {
        stream::iter(msgs.into_iter().map(Ok)).boxed()
    }

    fn parse_server(msg: &Message) -> WsServerMessage {
        match msg {
            Message::Text(t) => serde_json::from_str(t).expect("parse server msg"),
            _ => panic!("expected text frame, got {msg:?}"),
        }
    }

    #[test]
    fn client_message_typing_round_trip() {
        let m = WsClientMessage::Typing { typing: true };
        let s = serde_json::to_string(&m).unwrap();
        assert!(s.contains("\"type\":\"typing\""));
        let back: WsClientMessage = serde_json::from_str(&s).unwrap();
        match back {
            WsClientMessage::Typing { typing } => assert!(typing),
            _ => panic!("expected typing"),
        }
    }

    #[test]
    fn client_message_stop_no_payload() {
        let s = "{\"type\":\"stop\"}";
        let parsed: WsClientMessage = serde_json::from_str(s).unwrap();
        assert!(matches!(parsed, WsClientMessage::Stop));
    }

    #[test]
    fn server_message_pong_serializes_type_tag() {
        let m = WsServerMessage::Pong { ts: 42 };
        let s = serde_json::to_string(&m).unwrap();
        assert!(s.contains("\"type\":\"pong\""));
        assert!(s.contains("\"ts\":42"));
    }

    #[tokio::test]
    async fn loop_sends_hello_then_closes_on_client_close() {
        // 客户端立即发 Close → loop 退出,首帧应该是 hello。
        let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
        let sink = sink_from_tx(tx);
        let stream = stream_from_vec(vec![Message::Close(None)]);
        let state = fake_state();

        let res = ws_loop_inner(sink, stream, state, "u1".into()).await;
        assert!(matches!(res, Err(WsLoopError::SocketClosed)));

        let first = rx.recv().await.expect("hello frame");
        let parsed = parse_server(&first);
        match parsed {
            WsServerMessage::Hello { payload } => {
                assert_eq!(payload.user_id, "u1");
                assert_eq!(payload.protocol, "v1"); // hello_payload 返回 protocol=v1
            }
            _ => panic!("expected hello"),
        }
    }

    #[tokio::test]
    async fn ping_message_triggers_pong_response() {
        let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
        let sink = sink_from_tx(tx);
        let stream = stream_from_vec(vec![
            Message::Text(serde_json::to_string(&WsClientMessage::Ping { ts: Some(7) }).unwrap()),
            Message::Close(None),
        ]);
        let state = fake_state();

        let _ = ws_loop_inner(sink, stream, state, "u2".into()).await;

        // 第一帧是 hello,第二帧应该是 pong。
        let _hello = rx.recv().await.expect("hello");
        let pong = rx.recv().await.expect("pong");
        let parsed = parse_server(&pong);
        assert!(matches!(parsed, WsServerMessage::Pong { .. }));
    }

    #[tokio::test]
    async fn stop_message_sets_permissions_stop_signal() {
        let (tx, _rx) = mpsc::unbounded_channel::<Message>();
        let sink = sink_from_tx(tx);
        let stream = stream_from_vec(vec![
            Message::Text(serde_json::to_string(&WsClientMessage::Stop).unwrap()),
            Message::Close(None),
        ]);
        let state = fake_state();
        let user = "u3";
        // 预先创建 state，以便 stop 能写 permissions.stop_signal。
        let _ = state.state_store.get_or_create(user).await;

        let _ = ws_loop_inner(sink, stream, state.clone(), user.into()).await;

        let shared = state.state_store.get(user).unwrap();
        let st = shared.read();
        let v = st.get_path("permissions.stop_signal");
        assert_eq!(v, Some(Value::Bool(true)));
    }

    #[tokio::test]
    async fn typing_message_broadcasts_custom_bus_event() {
        let (tx, _rx) = mpsc::unbounded_channel::<Message>();
        let sink = sink_from_tx(tx);
        let stream = stream_from_vec(vec![
            Message::Text(serde_json::to_string(&WsClientMessage::Typing { typing: true }).unwrap()),
            Message::Close(None),
        ]);
        let state = fake_state();
        let mut bus_rx = state.state_store.subscribe();

        let _ = ws_loop_inner(sink, stream, state, "u4".into()).await;

        // 应该能从 bus 收到 typing custom event。
        let ev = bus_rx.recv().await.expect("typing bus event");
        match ev {
            StateEvent::Custom { user_id, event_type, payload } => {
                assert_eq!(user_id, "u4");
                assert_eq!(event_type, "typing");
                assert_eq!(payload["typing"], true);
            }
            _ => panic!("expected Custom typing event"),
        }
    }

    #[tokio::test]
    async fn state_change_event_forwards_to_client() {
        // 发布一条 StateEvent::Updated,客户端应该收到 state_change 帧。
        let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
        let sink = sink_from_tx(tx);
        let state = fake_state();
        let user = "u5";

        // 先 publish 一条,再让 client close —— bus_rx 是 broadcast,
        // 已经在 ws_loop_inner 里 subscribe 之前 publish 的不会被收到。
        // 所以策略改成:先开 loop(在 tokio task 里),再 publish,然后 close。
        let state_clone = state.clone();
        let user_clone = user.to_string();

        // 用 mpsc 给 stream 推帧,以便我们晚一点 close。
        let (frame_tx, frame_rx) = mpsc::unbounded_channel::<Result<Message, axum::Error>>();
        let frame_stream = tokio_stream::wrappers::UnboundedReceiverStream::new(frame_rx);
        let handle = tokio::spawn(async move {
            let _ = ws_loop_inner(sink, frame_stream, state_clone, user_clone).await;
        });

        // 等 loop 起来发出 hello。
        let _hello = rx.recv().await.expect("hello");

        // 发布一条 user_id="u5" 的事件。
        state.state_store.bus().publish(StateEvent::Updated {
            user_id: user.into(),
            version: 99,
        });

        // 收到 state_change。
        let frame = tokio::time::timeout(Duration::from_secs(2), rx.recv())
            .await
            .expect("timeout waiting state_change")
            .expect("recv");
        let parsed = parse_server(&frame);
        match parsed {
            WsServerMessage::StateChange { payload } => {
                assert_eq!(payload.topic, "state");
                assert_eq!(payload.op, "updated");
                assert_eq!(payload.user_id, user);
                assert_eq!(payload.payload["version"], 99);
            }
            other => panic!("expected state_change, got {other:?}"),
        }

        // 关 stream。
        frame_tx.send(Ok(Message::Close(None))).ok();
        drop(frame_tx);
        let _ = tokio::time::timeout(Duration::from_secs(2), handle).await;
    }

    #[tokio::test]
    async fn invalid_json_yields_error_frame() {
        let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
        let sink = sink_from_tx(tx);
        let stream = stream_from_vec(vec![
            Message::Text("not json".into()),
            Message::Close(None),
        ]);
        let state = fake_state();

        let _ = ws_loop_inner(sink, stream, state, "u6".into()).await;

        let _hello = rx.recv().await.expect("hello");
        let err_frame = rx.recv().await.expect("error frame");
        let parsed = parse_server(&err_frame);
        match parsed {
            WsServerMessage::Error { payload } => {
                assert_eq!(payload.code, crate::error_codes::BAD_REQUEST);
                assert!(payload.detail.contains("invalid client message"));
            }
            other => panic!("expected error, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn cross_user_state_event_filtered_out() {
        // 给 u7 起 loop,但发 user_id="other" 的事件 — 不应该收到 state_change。
        let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
        let sink = sink_from_tx(tx);
        let state = fake_state();

        let (frame_tx, frame_rx) = mpsc::unbounded_channel::<Result<Message, axum::Error>>();
        let frame_stream = tokio_stream::wrappers::UnboundedReceiverStream::new(frame_rx);
        let state_clone = state.clone();
        let handle = tokio::spawn(async move {
            let _ = ws_loop_inner(sink, frame_stream, state_clone, "u7".into()).await;
        });

        let _hello = rx.recv().await.expect("hello");

        state.state_store.bus().publish(StateEvent::Updated {
            user_id: "other".into(),
            version: 1,
        });

        // 100ms 内不应该收到任何帧。
        let got = tokio::time::timeout(Duration::from_millis(150), rx.recv()).await;
        assert!(got.is_err(), "should not receive cross-user event, got {got:?}");

        frame_tx.send(Ok(Message::Close(None))).ok();
        drop(frame_tx);
        let _ = tokio::time::timeout(Duration::from_secs(2), handle).await;
    }
}
