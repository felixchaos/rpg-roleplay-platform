//! store.rs — 按 user_id 分片的 GameState 存储
//!
//! 对应 Python: 全局 `_state_by_user: dict[str, GameState]` + `_state_lock`。
//! Rust 侧用 `DashMap<String, Arc<RwLock<GameState>>>` 取消全局可变状态,
//! 每个 user 独立 lock,避免一个用户的写卡住所有用户。
//!
//! ## key 类型:务实保留 `String`(不改 `rpg_core::UserId`)
//! 本 store 的 key **不是** `UserId`,而是 `String`:routes 的 `user_id_or_anon`
//! 在未登录时回落 `"anonymous"` 哨兵,该值没有对应 `users.id`,无法表达成
//! `UserId(i64)`。强行 `UserId` 化会破坏匿名游玩路径。因此这层保持 `String`,
//! `UserId` 化止步于 routes 边界(`user.id.to_string()` 经 `Display` 落进来)。
//! TODO(可选,后续 6B-x):若决定匿名也分配负数 / 保留 id,再将 key 升为 `UserId`
//! 并同步改 routes 的取 id 逻辑。
//!
//! 设计:
//! - 顶层 DashMap 已经是分片 lock,get_or_create 在不存在时插入空白存档。
//! - 内层 `Arc<RwLock<GameState>>` 让调用方按需 read/write,跨 await 持锁
//!   用 `parking_lot::RwLock` 同步锁;async 上下文里短时持锁拿读快照足够。
//!
//! ## 状态外置(6C-1):read-through cache + flush
//! 此前本 store **纯进程内**:`get_or_create` 只建空白存档,写入从不落库 —— 多 pod
//! 不共享状态、pod 重启全丢,是头号可伸缩障碍。现接 DB 做 read-through:
//!   - **加载**:cache miss 时 `await` 注入的 [`StateLoader`] 闭包从持久层拉存档;
//!     命中则插入,未命中(`None`)退化为空白存档。
//!   - **落库**:写路径标记 dirty;[`Self::flush`] 调注入的 [`StateSaver`] 闭包持久化
//!     后清 dirty(由 `/api/save` handler 触发)。
//!
//! ## 架构铁律:**rpg-state 不依赖 rpg-platform**(否则 platform→state→platform 循环)
//! 加载/落库的"具体怎么读写 DB"逻辑住在上层(rpg-server 装配时),本 crate 只持有
//! **依赖注入的闭包**([`StateLoader`] / [`StateSaver`]):签名只认 `&str` user_id 与
//! `GameState`,完全不提 rpg-platform 类型。装配层(rpg-server)把 `save_io::read_save`
//! / `game_saves.state_snapshot` 写回包成闭包注入。无 loader/saver 时(纯内存)行为与
//! 旧版完全一致,53 测试零回归。

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use ahash::RandomState as AHashState;
use dashmap::DashMap;
use parking_lot::RwLock;
use tokio::sync::broadcast;

use crate::bus::{StateEvent, StateEventBus};
use crate::ops::{apply_op, ApplyKind, ApplyOutcome, Op, OpError};
use crate::state::GameState;

pub type SharedState = Arc<RwLock<GameState>>;

/// 注入的「从持久层加载某 user 存档」闭包。
///
/// 入参是 store 的 `String` user_id(已登录用户为 `users.id` 的 Display,匿名为
/// `"anonymous"` 哨兵 —— 闭包内部自行决定匿名是否可加载)。返回 `Some(GameState)`
/// 表示命中持久化存档,`None` 表示无存档(store 退化为创建空白存档)。
pub type StateLoader =
    Arc<dyn Fn(String) -> Pin<Box<dyn Future<Output = Option<GameState>> + Send>> + Send + Sync>;

/// 注入的「把某 user 存档落库」闭包。入参为 user_id 与待持久化的 [`GameState`]。
pub type StateSaver =
    Arc<dyn Fn(String, GameState) -> Pin<Box<dyn Future<Output = ()> + Send>> + Send + Sync>;

/// 按 user_id 分片的 GameState 集合 + 嵌入的 state-event bus。
///
/// 替代 Python 全局 `_state_by_user`。bus 字段对外暴露 [`Self::subscribe`],
/// 任何写入路径(`apply_op_for_user` / 入口方法)统一从这里 publish 事件。
#[derive(Clone)]
pub struct StateStore {
    inner: Arc<DashMap<String, SharedState, AHashState>>,
    bus: StateEventBus,
    /// dirty 集合:写入后登记,flush 落库后清除。值无意义,仅用 key 作集合。
    dirty: Arc<DashMap<String, (), AHashState>>,
    /// 注入的加载闭包(read-through);None 时纯内存(测试 / 无 DB 部署)。
    loader: Option<StateLoader>,
    /// 注入的落库闭包;None 时 flush 为 no-op(纯内存)。
    saver: Option<StateSaver>,
}

// `dyn Fn` 不实现 Debug,手写一个不暴露闭包的实现。
impl std::fmt::Debug for StateStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("StateStore")
            .field("entries", &self.inner.len())
            .field("dirty", &self.dirty.len())
            .field("has_loader", &self.loader.is_some())
            .field("has_saver", &self.saver.is_some())
            .finish()
    }
}

impl StateStore {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(DashMap::with_hasher(AHashState::default())),
            bus: StateEventBus::new(),
            dirty: Arc::new(DashMap::with_hasher(AHashState::default())),
            loader: None,
            saver: None,
        }
    }
}

impl Default for StateStore {
    fn default() -> Self {
        Self::new()
    }
}

impl StateStore {

    /// 自定义 bus 容量(默认 256)— 给高吞吐场景留口。
    pub fn with_bus_capacity(capacity: usize) -> Self {
        Self {
            inner: Arc::new(DashMap::with_hasher(AHashState::default())),
            bus: StateEventBus::with_capacity(capacity),
            dirty: Arc::new(DashMap::with_hasher(AHashState::default())),
            loader: None,
            saver: None,
        }
    }

    /// 注入持久化加载/落库闭包(装配层 rpg-server 调用)。
    ///
    /// 这是打破 `rpg-state → rpg-platform` 循环的关键:本 crate 只认闭包签名,
    /// 不 import 任何 platform 类型;`save_io` 的真实调用在 rpg-server 装配时绑定。
    pub fn with_persistence(mut self, loader: StateLoader, saver: StateSaver) -> Self {
        self.loader = Some(loader);
        self.saver = Some(saver);
        self
    }

    /// 是否启用了持久化(已注入 loader/saver)。
    pub fn is_persistent(&self) -> bool {
        self.loader.is_some() && self.saver.is_some()
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
                // 写入成功 → 标记 dirty,等 flush 落库(跨 pod 持久化)。
                self.mark_dirty(user_id);
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
                // pending 写入也改了 state(pending_writes 队列),标记 dirty。
                self.mark_dirty(user_id);
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

    /// 拿到 user_id 对应的 state;cache miss 时**先经注入的 loader 从持久层加载**,
    /// 没有持久存档(或未注入 loader)才创建空白存档。
    ///
    /// ## read-through 流程
    /// 1. fast path:cache 命中直接返回(只读锁,不碰 loader)。
    /// 2. cache miss:若注入了 [`StateLoader`],`await` 它从 DB 拉存档(此处可能阻塞在
    ///    I/O,故方法 async)。注意:加载在 dashmap entry 写锁**之外**完成,避免持锁
    ///    跨 await(parking_lot 锁不可跨 await);加载完再用 entry API 原子回填。
    /// 3. 回填用 `entry(...).or_insert_with(...)`:即便多个 task 同时 miss 并各自加载,
    ///    最终只有一个 GameState 落进 store,其余 task 拿到现成 Arc(各自加载的副本丢弃,
    ///    与 DB 一致,无副作用)。
    ///
    /// 纯内存(无 loader)时退化为原行为:miss 即建空白存档。
    #[tracing::instrument(skip(self), fields(user_id = %user_id))]
    pub async fn get_or_create(&self, user_id: &str) -> SharedState {
        // fast path:已存在直接返回(避免拿 entry 的写锁,也跳过 loader)
        if let Some(existing) = self.inner.get(user_id) {
            return Arc::clone(existing.value());
        }

        // cache miss:先在锁外 await loader 从持久层加载(若注入)。
        let loaded: Option<GameState> = match &self.loader {
            Some(load) => {
                let fut = load(user_id.to_string());
                fut.await
            }
            None => None,
        };
        let loaded_hit = loaded.is_some();

        // slow path:entry API 保证对单个 user_id 只回填一份。
        let shared = {
            let entry = self.inner.entry(user_id.to_string()).or_insert_with(|| {
                let state = loaded.unwrap_or_else(|| GameState::new(user_id.to_string()));
                Arc::new(RwLock::new(state))
            });
            Arc::clone(entry.value())
        };

        if loaded_hit {
            tracing::debug!(user_id = %user_id, "state read-through: 命中持久层存档");
        }
        shared
    }

    /// 标记某 user 的 state 为 dirty(待 flush 落库)。写路径内部调用;
    /// 外部直接 `SharedState.write()` 改完 state 后也应调一次(routes 装配处接线)。
    pub fn mark_dirty(&self, user_id: &str) {
        self.dirty.insert(user_id.to_string(), ());
    }

    /// 某 user 是否有未落库的写入。
    pub fn is_dirty(&self, user_id: &str) -> bool {
        self.dirty.contains_key(user_id)
    }

    /// 把某 user 的 state 落库(经注入的 [`StateSaver`]),成功后清 dirty。
    ///
    /// 返回 `true` 表示确实落库(有 saver 且 state 存在);`false` 表示纯内存或无 state。
    /// `/api/save` handler 调用;后续可扩展为后台周期 flush / 写时直落。
    #[tracing::instrument(skip(self), fields(user_id = %user_id))]
    pub async fn flush(&self, user_id: &str) -> bool {
        let Some(save) = self.saver.clone() else {
            return false;
        };
        // 拿当前 state 的深拷贝快照(锁内 clone,锁外 await 落库,杜绝跨 await 持锁)。
        let snapshot: Option<GameState> = self
            .inner
            .get(user_id)
            .map(|r| r.value().read().clone());
        let Some(state) = snapshot else {
            return false;
        };
        save(user_id.to_string(), state).await;
        self.dirty.remove(user_id);
        tracing::debug!(user_id = %user_id, "state flush: 已落库");
        true
    }

    /// flush 所有 dirty 的 user(graceful shutdown / 周期任务用)。返回落库条数。
    pub async fn flush_all_dirty(&self) -> usize {
        let ids: Vec<String> = self.dirty.iter().map(|r| r.key().clone()).collect();
        let mut n = 0;
        for id in ids {
            if self.flush(&id).await {
                n += 1;
            }
        }
        n
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

    // ── 6C-1 状态外置 ──────────────────────────────────────────────────

    use std::sync::atomic::{AtomicUsize, Ordering};

    #[tokio::test]
    async fn test_pure_memory_degradation_no_persistence() {
        // 无 loader/saver(纯内存):get_or_create 建空白档,flush no-op。
        let store = StateStore::new();
        assert!(!store.is_persistent());
        let shared = store.get_or_create("u1").await;
        assert_eq!(shared.read().version, 0); // 空白档
        // 无 saver:flush 返回 false,不 panic。
        assert!(!store.flush("u1").await);
    }

    #[tokio::test]
    async fn test_read_through_loads_from_loader_on_miss() {
        // loader 命中:cache miss 时从"持久层"加载,不建空白档。
        let load_calls = Arc::new(AtomicUsize::new(0));
        let lc = Arc::clone(&load_calls);
        let loader: StateLoader = Arc::new(move |uid: String| {
            let lc = Arc::clone(&lc);
            Box::pin(async move {
                lc.fetch_add(1, Ordering::SeqCst);
                // 造一个非空快照,version 模拟已存档(用 from_value)。
                let mut gs = GameState::new(uid);
                let _ = gs.set_path("memory.main_quest", serde_json::json!("loaded-quest"));
                Some(gs)
            })
        });
        let saver: StateSaver = Arc::new(|_uid, _gs| Box::pin(async {}));
        let store = StateStore::new().with_persistence(loader, saver);

        let shared = store.get_or_create("u-load").await;
        assert_eq!(
            shared
                .read()
                .get_path("memory.main_quest")
                .and_then(|v| v.as_str()),
            Some("loaded-quest"),
            "miss 时应从 loader 加载存档"
        );
        assert_eq!(load_calls.load(Ordering::SeqCst), 1);

        // 第二次 get_or_create 走 fast-path,不再调 loader。
        let _ = store.get_or_create("u-load").await;
        assert_eq!(load_calls.load(Ordering::SeqCst), 1, "命中缓存不应再调 loader");
    }

    #[tokio::test]
    async fn test_flush_invokes_saver_and_clears_dirty() {
        // 写后 dirty;flush 调 saver 并清 dirty。
        let saved = Arc::new(AtomicUsize::new(0));
        let sc = Arc::clone(&saved);
        let loader: StateLoader = Arc::new(|_uid| Box::pin(async { None }));
        let saver: StateSaver = Arc::new(move |_uid: String, gs: GameState| {
            let sc = Arc::clone(&sc);
            Box::pin(async move {
                // 落库的快照应含已写入的字段。
                assert_eq!(
                    gs.get_path("memory.main_quest").and_then(|v| v.as_str()),
                    Some("q")
                );
                sc.fetch_add(1, Ordering::SeqCst);
            })
        });
        let store = StateStore::new().with_persistence(loader, saver);

        // loader 返回 None → 建空白档。
        let _ = store.get_or_create("u-save").await;
        assert!(!store.is_dirty("u-save"));
        let op = Op::Set {
            path: "memory.main_quest".to_string(),
            value: serde_json::json!("q"),
        };
        store.apply_op_for_user("u-save", op, "user", true).unwrap();
        assert!(store.is_dirty("u-save"), "写后应 dirty");

        assert!(store.flush("u-save").await);
        assert_eq!(saved.load(Ordering::SeqCst), 1, "flush 应调 saver 一次");
        assert!(!store.is_dirty("u-save"), "flush 后应清 dirty");
    }

    #[tokio::test]
    async fn test_snapshot_arc_shared_until_write() {
        // Arc 快照:无写入时连续 snapshot() 共享同一 Arc;写后失效重建。
        let store = StateStore::new();
        let shared = store.get_or_create("u-snap").await;
        let (a, b) = {
            let g = shared.read();
            (g.snapshot(), g.snapshot())
        };
        assert!(Arc::ptr_eq(&a, &b), "无写入时两次 snapshot 应共享同一 Arc");
        // 写入使快照失效。
        {
            let mut g = shared.write();
            let _ = g.set_path("turn", serde_json::json!(1));
        }
        let c = shared.read().snapshot();
        assert!(!Arc::ptr_eq(&a, &c), "写后 snapshot 应重建新 Arc");
        assert_eq!(c.get("turn").and_then(|v| v.as_i64()), Some(1));
    }
}
