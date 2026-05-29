//! bus.rs — state-events 广播总线
//!
//! 对应 Python: `rpg/server/state_events.py` 的 SSE 推送(版本号 broadcast)。
//!
//! 设计:
//! - [`StateEventBus`] 内部持有 `tokio::sync::broadcast::Sender<StateEvent>`,
//!   适合「写多/读多」+「订阅方可丢」的 fan-out 模型。每个订阅 channel 容量
//!   独立,即使一个慢消费者 lag 也不阻塞 publisher。
//! - 嵌入 [`crate::store::StateStore`],apply_op 成功后 publish。Cargo 没拆服务,
//!   订阅端在 rpg-server SSE 路由 / 测试 / 后台 worker 自己拉。
//! - 事件枚举刻意做扁平 — 不暴露完整 `GameState` 引用(那东西在 Arc<RwLock<>> 后面),
//!   只带最少索引(user_id + version)+ 业务摘要(Op / question id 等)。订阅方需要
//!   完整 state 自己从 `StateStore::get(&user_id)` 取读快照。
//!
//! 用法:
//! ```ignore
//! let store = StateStore::new();
//! let mut rx = store.subscribe();
//! tokio::spawn(async move {
//!     while let Ok(ev) = rx.recv().await {
//!         match ev {
//!             StateEvent::Updated { user_id, version } => { ... }
//!             _ => {}
//!         }
//!     }
//! });
//! ```

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::broadcast;

#[cfg(feature = "ts-rs")]
use ts_rs::TS;

use crate::ops::Op;

/// 单次 broadcast channel 容量。订阅方落后超过这个数会拿到 `RecvError::Lagged`。
const DEFAULT_CHANNEL_CAPACITY: usize = 256;

/// state-event bus,内部 `broadcast::Sender` 共享给所有订阅。
#[derive(Debug, Clone)]
pub struct StateEventBus {
    tx: broadcast::Sender<StateEvent>,
}

impl StateEventBus {
    /// 默认容量(256)。如果有特殊吞吐需要可用 [`Self::with_capacity`]。
    pub fn new() -> Self {
        Self::with_capacity(DEFAULT_CHANNEL_CAPACITY)
    }

    pub fn with_capacity(capacity: usize) -> Self {
        let (tx, _) = broadcast::channel(capacity);
        Self { tx }
    }

    /// 发布一条事件。如果当前没有任何活跃订阅者,broadcast 会 OK 返回 0 listener,
    /// 不算错误 — bus 是 fire-and-forget。
    pub fn publish(&self, event: StateEvent) {
        // err 仅在 0 receivers 时触发,业务层不关心
        let _ = self.tx.send(event);
    }

    /// 新建一个订阅 channel。多调用方可以并行订阅,每个 receiver 独立。
    pub fn subscribe(&self) -> broadcast::Receiver<StateEvent> {
        self.tx.subscribe()
    }

    /// 当前活跃订阅数(可用于 metrics / debug)。
    pub fn receiver_count(&self) -> usize {
        self.tx.receiver_count()
    }
}

impl Default for StateEventBus {
    fn default() -> Self {
        Self::new()
    }
}

/// 状态变更事件。
///
/// 设计取舍:
/// - `Updated` 是最粗粒度的「state 变了」信号,只带 user_id + version。SSE 端
///   订阅这个就够触发前端 refetch。
/// - `OpApplied` 携带具体 op,给 LeftRail / audit 实时反馈用。
/// - `PendingAdded` / `PendingResolved` 给审批 UI 用。
/// - `QuestionAdded` / `QuestionAnswered` 给 pending-question 卡片用。
/// - `TimelineJump` / `WorldlineValidation` 给世界线推演 UI 用。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "ts-rs", derive(TS))]
#[cfg_attr(
    feature = "ts-rs",
    ts(export, export_to = "../../../../frontend/src/types/rust/events/")
)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum StateEvent {
    /// 任意状态变化,带 user_id + 新 version,前端按此 refetch。
    Updated {
        user_id: String,
        version: u64,
    },
    /// 一个具体的 [`Op`] 通过闸门并已写入。
    OpApplied {
        user_id: String,
        version: u64,
        op: Op,
        source: String,
    },
    /// 写入被拦截,进了 pending_writes 队列(等审批)。
    PendingAdded {
        user_id: String,
        pending_id: String,
        path: String,
        source: String,
    },
    /// 一条 pending_write 已被 approve / reject 处理。
    PendingResolved {
        user_id: String,
        pending_id: String,
        approved: bool,
        path: String,
    },
    /// 新询问入队(GM / 系统问玩家)。
    QuestionAdded {
        user_id: String,
        question_id: String,
        question: String,
        source: String,
    },
    /// 玩家回答 / 跳过询问。
    QuestionAnswered {
        user_id: String,
        question_id: String,
        choice: Option<String>,
    },
    /// 时间跳跃状态机更新(request / confirm / reject)。
    TimelineJump {
        user_id: String,
        anchor_state: String,
        world_time: String,
    },
    /// 世界线设定校验结果更新(passed / conflict / review / none)。
    WorldlineValidation {
        user_id: String,
        status: String,
        message: String,
    },
    /// 任意自定义事件(为后续扩展留口,带 payload)。
    Custom {
        user_id: String,
        /// 自定义事件子类型(避免与 serde tag 字段 `kind` 同名)。
        event_type: String,
        payload: Value,
    },
}

impl StateEvent {
    /// 大部分事件都带 user_id,提供统一访问。
    pub fn user_id(&self) -> &str {
        match self {
            StateEvent::Updated { user_id, .. }
            | StateEvent::OpApplied { user_id, .. }
            | StateEvent::PendingAdded { user_id, .. }
            | StateEvent::PendingResolved { user_id, .. }
            | StateEvent::QuestionAdded { user_id, .. }
            | StateEvent::QuestionAnswered { user_id, .. }
            | StateEvent::TimelineJump { user_id, .. }
            | StateEvent::WorldlineValidation { user_id, .. }
            | StateEvent::Custom { user_id, .. } => user_id,
        }
    }
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
