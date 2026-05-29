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

use crate::state::GameState;

pub type SharedState = Arc<RwLock<GameState>>;

/// 按 user_id 分片的 GameState 集合。
///
/// 替代 Python 全局 `_state_by_user`。
#[derive(Debug, Default, Clone)]
pub struct StateStore {
    inner: Arc<DashMap<String, SharedState>>,
}

impl StateStore {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(DashMap::new()),
        }
    }

    /// 拿到 user_id 对应的 state,不存在则创建空白存档。
    ///
    /// 异步签名留口:未来需要从持久层(rpg-db)惰性 load 时,在此处 await。
    /// 当前实现纯内存,不会阻塞。
    pub async fn get_or_create(&self, user_id: &str) -> SharedState {
        if let Some(existing) = self.inner.get(user_id) {
            return Arc::clone(existing.value());
        }
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
