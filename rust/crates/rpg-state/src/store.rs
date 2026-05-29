//! store.rs — 按 user_id 分片的 GameState 存储
//!
//! 对应 Python: 全局 `_state_by_user: dict[str, GameState]` + `_state_lock`。
//! Rust 侧用 `DashMap<UserId, Arc<RwLock<GameState>>>` 取消全局可变状态,
//! 每个 user 独立 lock,避免一个用户的写卡住所有用户。
//!
//! 设计:
//! - 顶层 DashMap 已经是分片 lock,get_or_create 在不存在时插入空白存档。
//! - 内层 `Arc<RwLock<GameState>>` 让调用方按需 read/write,跨 await 持锁
//!   用 `parking_lot::RwLock` 同步锁;async 上下文里短时持锁拿读快照足够。
//! - 持久化(SAVE_FILE) TODO:Python 侧每次 mutate 后同步写盘,Rust 改为
//!   后台 flush task 或事务接缝(由 rpg-db crate 接管)。

use std::sync::Arc;

use dashmap::DashMap;
use parking_lot::RwLock;
use tokio::sync::broadcast;

use crate::bus::{StateEvent, StateEventBus};
use crate::ops::{apply_op, ApplyKind, ApplyOutcome, Op, OpError};
use crate::state::GameState;

pub type SharedState = Arc<RwLock<GameState>>;

/// 按 user_id 分片的 GameState 集合 + 嵌入的 state-event bus。
///
/// 替代 Python 全局 `_state_by_user`。bus 字段对外暴露 [`Self::subscribe`],
/// 任何写入路径(`apply_op_for_user` / 入口方法)统一从这里 publish 事件。
#[derive(Debug, Default, Clone)]
pub struct StateStore {
    inner: Arc<DashMap<String, SharedState>>,
    bus: StateEventBus,
}

impl StateStore {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(DashMap::new()),
            bus: StateEventBus::new(),
        }
    }

    /// 自定义 bus 容量(默认 256)— 给高吞吐场景留口。
    pub fn with_bus_capacity(capacity: usize) -> Self {
        Self {
            inner: Arc::new(DashMap::new()),
            bus: StateEventBus::with_capacity(capacity),
        }
    }

    /// 暴露内部 bus,供需要直接 publish 自定义事件的调用方使用。
    pub fn bus(&self) -> &StateEventBus {
        &self.bus
    }

    /// 订阅 state-event 流。每次调用拿到独立 receiver,慢消费者会拿到
    /// `RecvError::Lagged` 但不会阻塞 publisher。
    pub fn subscribe(&self) -> broadcast::Receiver<StateEvent> {
        self.bus.subscribe()
    }

    /// 应用 op 到指定 user 的 state,成功后 publish 事件。
    ///
    /// 失败(OpError)不发事件;Pending / Rejected 也不发 `Updated`,
    /// 但会发 [`StateEvent::PendingAdded`] 让 UI 即时看见排队中的写入。
    #[tracing::instrument(
        skip(self, op),
        fields(
            user_id = %user_id,
            op_type = ?std::mem::discriminant(&op),
            path = %op.path(),
            source = %source,
            force = force,
        )
    )]
    pub fn apply_op_for_user(
        &self,
        user_id: &str,
        op: Op,
        source: &str,
        force: bool,
    ) -> Result<ApplyOutcome, OpError> {
        let shared = match self.inner.get(user_id) {
            Some(r) => Arc::clone(r.value()),
            None => {
                let entry = self.inner.entry(user_id.to_string()).or_insert_with(|| {
                    Arc::new(RwLock::new(GameState::new(user_id.to_string())))
                });
                Arc::clone(entry.value())
            }
        };
        let (outcome, version, pending_id) = {
            let mut guard = shared.write();
            let outcome = apply_op(&mut guard, op.clone(), source, force)?;
            let version = guard.version;
            // pending 情况下从 permissions.pending_writes 尾部取最新 id
            let pending_id = if outcome.kind == ApplyKind::Pending {
                guard
                    .data
                    .get("permissions")
                    .and_then(|p| p.get("pending_writes"))
                    .and_then(|p| p.as_array())
                    .and_then(|arr| arr.last())
                    .and_then(|v| v.get("id"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            } else {
                None
            };
            (outcome, version, pending_id)
        };

        match outcome.kind {
            ApplyKind::Applied => {
                self.bus.publish(StateEvent::OpApplied {
                    user_id: user_id.to_string(),
                    version,
                    op,
                    source: source.to_string(),
                });
                self.bus.publish(StateEvent::Updated {
                    user_id: user_id.to_string(),
                    version,
                });
            }
            ApplyKind::Pending => {
                if let Some(pid) = pending_id {
                    self.bus.publish(StateEvent::PendingAdded {
                        user_id: user_id.to_string(),
                        pending_id: pid,
                        path: outcome.path.clone(),
                        source: source.to_string(),
                    });
                }
            }
            ApplyKind::Rejected => {}
        }
        Ok(outcome)
    }

    /// 发布一条 `Updated` 事件 — 用于外部直接 mutate state(`SharedState.write()`)
    /// 后通知订阅方。例如 directives / structured 等聚合调用走完所有 op,在最后
    /// 调一次让 SSE 收到一次性的 refetch 信号。
    pub fn notify_updated(&self, user_id: &str) {
        if let Some(s) = self.inner.get(user_id) {
            let version = s.read().version;
            self.bus.publish(StateEvent::Updated {
                user_id: user_id.to_string(),
                version,
            });
        }
    }

    /// 直接发布任意事件 — 给已经知道事件细节的入口(timeline_jump / question / pending)用。
    pub fn publish(&self, event: StateEvent) {
        self.bus.publish(event);
    }

    /// 拿到 user_id 对应的 state,不存在则创建空白存档。
    ///
    /// 异步签名留口:未来需要从持久层(rpg-db)惰性 load 时,在此处 await。
    /// 当前实现纯内存,不会阻塞。
    ///
    /// 并发安全:`DashMap::entry(...).or_insert_with(...)` 在 not-found 路径下
    /// 是原子 upsert,内部 `or_insert_with` 闭包对单个 user_id 只会被求值一次,
    /// 即使 N 个 task 同时撞进来,也只会创建一份 GameState。
    #[tracing::instrument(skip(self), fields(user_id = %user_id))]
    pub async fn get_or_create(&self, user_id: &str) -> SharedState {
        // fast path:已存在直接返回(避免拿 entry 的写锁)
        if let Some(existing) = self.inner.get(user_id) {
            return Arc::clone(existing.value());
        }
        // slow path:dashmap entry API 保证 or_insert_with 只对一个 task 求值
        let entry = self
            .inner
            .entry(user_id.to_string())
            .or_insert_with(|| Arc::new(RwLock::new(GameState::new(user_id.to_string()))));
        Arc::clone(entry.value())
    }

    /// 仅获取已存在的 state(不创建)。
    pub fn get(&self, user_id: &str) -> Option<SharedState> {
        self.inner.get(user_id).map(|r| Arc::clone(r.value()))
    }

    /// 显式插入(用于 rpg-db 从持久层加载完整存档后回填)。
    pub fn insert(&self, user_id: impl Into<String>, state: GameState) -> SharedState {
        let shared = Arc::new(RwLock::new(state));
        self.inner.insert(user_id.into(), Arc::clone(&shared));
        shared
    }

    pub fn remove(&self, user_id: &str) -> Option<SharedState> {
        self.inner.remove(user_id).map(|(_, v)| v)
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    /// 当前在线 user_id 快照(用于 admin / metrics)。
    pub fn user_ids(&self) -> Vec<String> {
        self.inner.iter().map(|r| r.key().clone()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[tokio::test(flavor = "multi_thread", worker_threads = 8)]
    async fn test_get_or_create_concurrent() {
        // 50 个 task 同时调用 same user_id,确保只创建 1 个 GameState。
        // dashmap 的 entry API 在 get-not-found 时是原子 upsert,
        // 内部 or_insert_with 只对单个 task 求值,其余拿到现成的 Arc。
        let store = Arc::new(StateStore::new());
        let user_id = "race_user";
        let mut handles = Vec::with_capacity(50);
        for _ in 0..50 {
            let store = Arc::clone(&store);
            handles.push(tokio::spawn(async move {
                store.get_or_create(user_id).await
            }));
        }
        let mut shares: Vec<SharedState> = Vec::with_capacity(50);
        for h in handles {
            shares.push(h.await.unwrap());
        }
        // 所有 task 必须拿到同一个 Arc 实例(指针相同)
        let first = Arc::clone(&shares[0]);
        for s in &shares[1..] {
            assert!(
                Arc::ptr_eq(&first, s),
                "get_or_create 在并发下创建了多个 GameState"
            );
        }
        // store 里只有 1 条记录
        assert_eq!(store.len(), 1);
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 8)]
    async fn test_apply_op_for_user_concurrent_single_state() {
        // 同时多 task 走 apply_op_for_user,也只创建一份 state。
        let store = Arc::new(StateStore::new());
        let user_id = "writer_user";
        let mut handles = Vec::with_capacity(50);
        for i in 0..50 {
            let store = Arc::clone(&store);
            handles.push(tokio::spawn(async move {
                let op = Op::Set {
                    path: "memory.facts".to_string(),
                    value: serde_json::json!(format!("fact_{i}")),
                };
                store.apply_op_for_user(user_id, op, "user", true)
            }));
        }
        for h in handles {
            let _ = h.await.unwrap();
        }
        assert_eq!(store.len(), 1);
        let shared = store.get(user_id).unwrap();
        let g = shared.read();
        // 50 次写入都被 apply,version 至少 50
        assert!(g.version >= 50, "version={} expected >=50", g.version);
    }
}
