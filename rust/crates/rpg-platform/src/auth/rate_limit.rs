//! 登录速率限制 —— 内存版,对应 Python `_FAIL_BUCKETS` + `_LOCKED_UNTIL`。
//!
//! Python 原版用 threading.Lock + monotonic time;Rust 这里用
//! `parking_lot::Mutex` + `tokio::time::Instant` 同义实现。
//!
//! 提供:
//! - `RateLimiter` (struct) — 状态机
//! - `RateLimitConfig` — 来自 `core::config` 的参数
//! - `RateLimited` (error) — 命中锁定时抛出
//! - `admin_unlock(ip, username)` — 暴露给 `/api/admin/login/unlock`

use std::collections::HashMap;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use thiserror::Error;

/// Python 里抛的 `RateLimited` 等价物。
#[derive(Debug, Error)]
#[error("too many failed logins; retry after {retry_after_sec}s")]
pub struct RateLimited {
    pub retry_after_sec: u64,
    pub key: String,
}

/// 配置(来自 `rpg_core::config`,默认值与 Python 一致)。
#[derive(Debug, Clone, Copy)]
pub struct RateLimitConfig {
    pub max_fails: u32,
    pub window: Duration,
    pub lockout: Duration,
}

impl RateLimitConfig {
    /// 直接从环境读(对应 Python `_login_max_fails()`/`_login_window_sec()`/`_login_lockout_sec()`)。
    pub fn from_env() -> Self {
        Self {
            max_fails: rpg_core::config::login_max_fails(),
            window: Duration::from_secs(rpg_core::config::login_window_sec()),
            lockout: Duration::from_secs(rpg_core::config::login_lockout_sec()),
        }
    }
}

impl Default for RateLimitConfig {
    fn default() -> Self {
        Self {
            max_fails: 5,
            window: Duration::from_secs(300),
            lockout: Duration::from_secs(60),
        }
    }
}

/// 速率限制器 — 持有内存状态,可共享(`Arc<RateLimiter>`)。
pub struct RateLimiter {
    cfg: RateLimitConfig,
    state: Mutex<State>,
}

#[derive(Default)]
struct State {
    fails: HashMap<String, Vec<Instant>>,
    locked_until: HashMap<String, Instant>,
}

fn bucket_key(ip: &str, username: &str) -> String {
    let ip_part = if ip.is_empty() { "-" } else { ip };
    format!("{}|{}", ip_part, username.to_lowercase())
}

impl RateLimiter {
    pub fn new(cfg: RateLimitConfig) -> Self {
        Self {
            cfg,
            state: Mutex::new(State::default()),
        }
    }

    /// 创建默认配置(从 env 读)。
    pub fn from_env() -> Self {
        Self::new(RateLimitConfig::from_env())
    }

    /// 对应 Python `_check_rate_limit`。命中锁定 → `Err(RateLimited)`。
    pub fn check(&self, ip: &str, username: &str) -> Result<(), RateLimited> {
        let key = bucket_key(ip, username);
        let now = Instant::now();
        let mut state = self.state.lock();

        if let Some(&unlock_at) = state.locked_until.get(&key) {
            if now < unlock_at {
                return Err(RateLimited {
                    retry_after_sec: (unlock_at - now).as_secs(),
                    key,
                });
            }
            state.locked_until.remove(&key);
        }
        // 清理窗口外条目
        if let Some(bucket) = state.fails.get_mut(&key) {
            bucket.retain(|t| now.duration_since(*t) < self.cfg.window);
        }
        Ok(())
    }

    /// 对应 Python `_record_login_fail`。返回当前窗口内失败次数;到阈值会锁定。
    pub fn record_fail(&self, ip: &str, username: &str) -> u32 {
        let key = bucket_key(ip, username);
        let now = Instant::now();
        let mut state = self.state.lock();
        let bucket = state.fails.entry(key.clone()).or_default();
        bucket.push(now);
        bucket.retain(|t| now.duration_since(*t) < self.cfg.window);
        let count = bucket.len() as u32;
        if count >= self.cfg.max_fails {
            state.locked_until.insert(key, now + self.cfg.lockout);
        }
        count
    }

    /// 对应 Python `_record_login_success` — 清空该 key 状态。
    pub fn record_success(&self, ip: &str, username: &str) {
        let key = bucket_key(ip, username);
        let mut state = self.state.lock();
        state.fails.remove(&key);
        state.locked_until.remove(&key);
    }

    /// 对应 Python `admin_unlock(ip, username)` — admin 主动解锁。
    pub fn admin_unlock(&self, ip: &str, username: &str) {
        self.record_success(ip, username);
    }
}

/// 进程级 default RateLimiter(对应 Python 顶层 `_FAIL_BUCKETS` 全局)。
pub static GLOBAL_LIMITER: once_cell::sync::Lazy<RateLimiter> =
    once_cell::sync::Lazy::new(RateLimiter::from_env);

/// 对应 Python 顶层 `admin_unlock(ip, username)` — 使用全局 limiter。
pub fn admin_unlock(ip: &str, username: &str) {
    GLOBAL_LIMITER.admin_unlock(ip, username);
    // TODO: 写 login_audit 行(admin_unlock 事件) — 等 rpg-db 暴露 audit helper
}
