//! sse_metrics — SSE 活跃连接数埋点。
//!
//! 指标:
//!   * `sse_active_connections` — Gauge, label `endpoint`(state_events/chat/opening/console)
//!
//! 使用方式:在每个 SSE handler 的开头调 [`SseConnectionGuard::new`],
//! 持有 guard 直到 handler 返回;axum 在客户端断开时 drop stream → drop guard → gauge dec。
//!
//! 由于 axum Sse::new 不暴露 on_close 钩子,我们把 guard 附着在 [`GuardedStream`] 上:
//! stream 本身 drop 时 guard 跟着 drop,从而触发 gauge dec。

use std::convert::Infallible;
use std::pin::Pin;
use std::task::{Context, Poll};

use axum::response::sse::Event;
use futures_util::stream::Stream;

/// RAII guard: 创建时 gauge +1,drop 时 gauge -1。
pub struct SseConnectionGuard {
    endpoint: &'static str,
}

impl SseConnectionGuard {
    pub fn new(endpoint: &'static str) -> Self {
        metrics::gauge!("sse_active_connections", "endpoint" => endpoint).increment(1.0);
        Self { endpoint }
    }
}

impl Drop for SseConnectionGuard {
    fn drop(&mut self) {
        metrics::gauge!("sse_active_connections", "endpoint" => self.endpoint).decrement(1.0);
    }
}

/// 把 `SseConnectionGuard` 附着在任意 SSE event stream 上。
/// stream exhausted / dropped → guard dropped → gauge dec。
///
/// `S` 只需满足 `Stream` + `Send`;不要求 `Unpin`,内部用 `Pin::new_unchecked`。
pub struct GuardedStream<S> {
    /// SAFETY: 字段不会被 move 出 `Pin`(只能通过 `as_mut` 访问)。
    inner: S,
    _guard: SseConnectionGuard,
}

impl<S: Unpin> GuardedStream<S> {
    pub fn new(inner: S, guard: SseConnectionGuard) -> Self {
        Self { inner, _guard: guard }
    }
}

impl<S: Stream<Item = Result<Event, Infallible>> + Unpin> Stream for GuardedStream<S> {
    type Item = Result<Event, Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        Pin::new(&mut self.inner).poll_next(cx)
    }
}

/// 把 `SseConnectionGuard` 附着在 `Pin<Box<dyn Stream>>` 上。
///
/// game.rs 里的 SSE handler 返回 `ReceiverStream`(Unpin),
/// core.rs 里通过 `.chain(...)` 得到的 stream 是 non-Unpin。
/// 此版本专门接受 boxed stream。
pub struct BoxedGuardedStream {
    inner: Pin<Box<dyn Stream<Item = Result<Event, Infallible>> + Send>>,
    _guard: SseConnectionGuard,
}

impl BoxedGuardedStream {
    pub fn new(
        inner: Pin<Box<dyn Stream<Item = Result<Event, Infallible>> + Send>>,
        guard: SseConnectionGuard,
    ) -> Self {
        Self { inner, _guard: guard }
    }
}

impl Stream for BoxedGuardedStream {
    type Item = Result<Event, Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.inner.as_mut().poll_next(cx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 验证 guard 在无 recorder 时不 panic。
    #[test]
    fn guard_inc_dec_no_panic() {
        let g = SseConnectionGuard::new("test");
        drop(g);
    }

    /// 验证 BoxedGuardedStream 构造 / drop 不 panic。
    #[test]
    fn boxed_guarded_stream_no_panic() {
        let inner: Pin<Box<dyn Stream<Item = Result<Event, Infallible>> + Send>> =
            Box::pin(futures_util::stream::empty());
        let guard = SseConnectionGuard::new("test");
        let _gs = BoxedGuardedStream::new(inner, guard);
        // drop here — no panic
    }

    /// GuardedStream (Unpin variant) 构造 / drop。
    #[test]
    fn guarded_stream_unpin_no_panic() {
        use tokio_stream::wrappers::ReceiverStream;
        let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(1);
        drop(tx);
        let inner = ReceiverStream::new(rx);
        let guard = SseConnectionGuard::new("test");
        let _gs = GuardedStream::new(inner, guard);
    }
}
