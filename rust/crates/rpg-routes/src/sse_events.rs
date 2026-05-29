//! sse_events.rs — SSE wire 事件 typed schema(导出给前端)。
//!
//! 后端 SSE 出口在 `game.rs` / `console_assistant.rs` / `core.rs` 通过
//! [`crate::named_sse_event`] 发出命名事件,payload 是动态 `serde_json::Value`。
//! 本模块把每个事件名对应的 payload **固化成 typed struct**,并通过 ts-rs
//! 导出到 `frontend/src/types/rust/events/`,前端按 `SseEnvelope` 解析。
//!
//! 这是**声明式契约** — 不强制 runtime 走 typed payload,后端继续用 json!,
//! 但任何字段改名 / 新增 / 移除都应该同步修改这里,并跑 `cargo test
//! -p rpg-routes --features ts-rs` 重新生成前端类型。CI 应跑 `ts-rs` 触发
//! 让前端 TS 编译炸出 mismatch,做 silent-break 兜底。
//!
//! 涉及事件名(与 `frontend/src/api-client.js` 监听对齐):
//! - `hello`         — 流首帧,握手 + reset 前端 backoff
//! - `state_change`  — 阶段切换 / phase 标签 / 会话 id 等
//! - `chunk`         — LLM 流式片段(text / tool_use / thinking)
//! - `done`          — 流末尾(可能携带最新 state snapshot)
//! - `error`         — 任意错误(detail + code)
//! - `keepalive`     — 长连接心跳(空 payload,前端忽略)
//!
//! 顶层 [`SseEnvelope`] 是 tagged union(event 名 + payload),给前端做
//! discriminated union 用。后端不直接构造 envelope — 由 `named_sse_event`
//! 单独构造 (event, payload),wire 形式就是 SSE 文本帧 + JSON。

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

// ── 单事件 payload ──────────────────────────────────────────────────────────

/// `hello` 事件 payload — 与 [`crate::hello_payload`] 同形。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct SseHelloPayload {
    /// 当前会话用户 id(匿名时 `"anonymous"`)。
    pub user_id: String,
    /// Unix 秒时间戳。
    pub ts: i64,
    /// 协议版本号,目前 `"v1"`。
    pub protocol: String,
}

/// `state_change` 事件 payload — 多形,实际由调用点选填字段。
///
/// 比如:
/// - opening:`{ phase: "generating", label: "GM 构思开场中…" }`
/// - chat:  `{ phase: "generating", label: "GM 思考中…" }`
/// - console_assistant chat:`{ conversation_id: "..." }`
/// - console_assistant confirm:`{ call_id: "...", decision: "approve" }`
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct SseStateChangePayload {
    /// 通用阶段标签(`generating` / `waiting` / `tool_call` 等)。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub phase: Option<String>,
    /// 人类可读阶段名(中文)。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub label: Option<String>,
    /// console_assistant chat 会话 id。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<String>,
    /// console_assistant confirm 工具调用 id。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub call_id: Option<String>,
    /// `approve` / `reject` / `cancel` 等。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decision: Option<String>,
}

/// `chunk` 事件 payload — LLM 流式片段。
///
/// 目前 game.rs stub 阶段只发 `{ text: "" }`。chat_pipeline 落地后会扩展为
/// `kind: "text" | "thinking" | "tool_call" | "usage" | "stop" | "error"`,
/// 与 [`rpg_llm::pipeline::WireChatChunk`] 对齐(那是 LLM 内部 ChatChunk 的
/// ts-rs 友好 wire 投影)。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct SseChunkPayload {
    /// 文本片段(text/thinking/error 公用)。
    #[serde(default)]
    pub text: String,
    /// 可选 kind,与 WireChatChunk 对齐(`text` / `thinking` / `tool_call` 等)。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    /// `tool_call` 类:已合并完的 tool 调用 id。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// `tool_call` 类:tool 名称。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    /// `tool_call` 类:已合并完的 tool input(JSON object)。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_input: Option<Value>,
}

/// `done` 事件 payload — 流末尾,可能携带最新 state snapshot。
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct SseDonePayload {
    /// `{ "state": GameStateData }` 或更宽 payload。typed snapshot 见
    /// `frontend/src/types/rust/GameStateData.ts`。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<Value>,
    /// 是否被跨 pod stop 信号中断。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interrupted: Option<bool>,
    /// console_assistant 通用 `{ ok: true }` 简化。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ok: Option<bool>,
}

/// `error` 事件 payload。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct SseErrorPayload {
    /// 错误描述(可中文)。
    pub detail: String,
    /// 机器可读 code(`bad_request` / `quota_exceeded` 等)。
    pub code: String,
}

// ── state-change bus 投影 ───────────────────────────────────────────────────

/// `state_change` 事件 — bus 投影 payload。
///
/// W3-2:`/api/state_events` SSE 通道把 [`rpg_state::StateEvent`] 投影成
/// `{ topic, op, payload, ts }` 形态推给前端,与 Python `state_event_bus.StateEvent.to_sse_data`
/// 保持线上兼容。前端 `state-event-bridge.js` 据 `topic` 派 `rpg-{topic}-updated` CustomEvent。
///
/// 与 [`SseStateChangePayload`] 区分:那个是流内阶段标签(chat / opening 等),
/// 这个是 bus → 前端的状态总线投影。两者共用同一个 SSE event name 是历史包袱,
/// 前端按字段是否存在区分(`phase` vs `topic`)。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
pub struct SseStateBusPayload {
    /// 业务 topic(`saves` / `cards` / `personas` / `permissions` / `pending` /
    /// `questions` / `timeline` / `worldline` / `state` 等)。
    pub topic: String,
    /// 操作(`updated` / `created` / `deleted` / `applied` / `added` / `resolved`
    /// / `answered` / `jump` / `validated` 等)。
    pub op: String,
    /// user_id(防 cross-user 泄漏,前端校验)。
    pub user_id: String,
    /// 关联载荷 — 与具体 [`rpg_state::StateEvent`] 变体相关字段(version / op /
    /// pending_id / question_id 等)。
    #[serde(default)]
    pub payload: Value,
    /// Unix 秒时间戳。
    pub ts: i64,
}

// ── 顶层 envelope ───────────────────────────────────────────────────────────

/// 顶层 SSE event 信封 — 前端做 discriminated union 用。
///
/// 注意:后端 [`crate::named_sse_event`] 写出的是裸 `event: xxx` + JSON
/// payload,**不是**整体 envelope JSON。这里给的是 frontend 反序列化后的
/// 逻辑形态:`{ event: "hello", payload: SseHelloPayload }`。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum SseEnvelope {
    Hello { payload: SseHelloPayload },
    StateChange { payload: SseStateChangePayload },
    Chunk { payload: SseChunkPayload },
    Done { payload: SseDonePayload },
    Error { payload: SseErrorPayload },
    /// 心跳帧,无 payload(axum KeepAlive 实际发 `: keepalive\n\n` 注释帧,
    /// 这里只是形式登记)。
    Keepalive,
}

#[cfg(test)]
mod tests {
    /// 触发 ts-rs 导出(--features ts-rs 时生效)。
    #[cfg(feature = "ts-rs")]
    #[test]
    fn export_ts_types() {
        // ts-rs 在 #[ts(export)] 时会通过 inventory/ctor 机制在测试结束后自动写文件。
    }
}
