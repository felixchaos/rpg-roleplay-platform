//! 登录速率限制 —— 对应 Python `_FAIL_BUCKETS` + `_LOCKED_UNTIL`。
//!
//! 待办A:失败计数下沉到 [`RateLimitBackend`](crate::infra::rate_limit) —— 默认进程内
//! Memory,设 `RPG_REDIS_URL` 则共享到 Redis(多副本统一防爆破)。
//!
//! ## 语义映射(为什么这样接 backend)
//! 登录限流是「失败桶 + 锁定」:窗口内失败 ≥ `max_fails` → 锁定 `lockout` 秒。把它映射到
//! 通用 [`RateLimitBackend::check_rate`](crate::infra::rate_limit::RateLimitBackend::check_rate)
//! 滑窗上:
//! - [`RateLimiter::record_fail`] = 在 `key` 的失败滑窗里登记一次命中。`check_rate` 返回
//!   `false`(命中数达到 `max_fails`)即视为「该锁定」—— 写一条**锁定标记**到第二个滑窗
//!   key(同样经 backend,多副本共享)。
//! - [`RateLimiter::check`] = 只读探测锁定标记。**不**登记失败命中(只有真失败才计)。
//! - [`RateLimiter::record_success`] = 清失败窗口 + 锁定标记。
//!
//! 失败计数与锁定标记都用 backend 的并发计数器(`incr`/读 `count`/`decr` 归零)承载,
//! 与 Python `bucket.push; if len >= max_fails: lock` 逐字对齐。计数随成功登录 / admin
//! 解锁清零;Redis 后端额外有 TTL 兜底,防进程崩溃后计数永久残留。
//!
//! 注:相比旧版的 `window` 滑窗,Memory 后端的失败计数不再随 `window` 自动老化(靠成功/
//! 解锁显式清零)。生产多副本应设 `RPG_REDIS_URL`,由计数 key 的 TTL 提供窗口式过期。

use std::time::Duration;

use thiserror::Error;

use crate::infra::rate_limit::{SharedBackend, GLOBAL_BACKEND};

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

/// 速率限制器 —— 失败计数经可插拔 [`RateLimitBackend`](crate::infra::rate_limit),可共享(`Arc<RateLimiter>`)。
pub struct RateLimiter {
    cfg: RateLimitConfig,
    backend: SharedBackend,
}

fn bucket_key(ip: &str, username: &str) -> String {
    let ip_part = if ip.is_empty() { "-" } else { ip };
    format!("{}|{}", ip_part, username.to_lowercase())
}

/// 失败滑窗 key。
fn fail_key(bucket: &str) -> String {
    format!("login:fail:{bucket}")
}
/// 锁定标记滑窗 key(`lockout` 秒里有命中即锁定)。
fn lock_key(bucket: &str) -> String {
    format!("login:lock:{bucket}")
}

impl RateLimiter {
    /// 用指定 backend 构造(测试可注入显式 Memory backend)。
    pub fn with_backend(cfg: RateLimitConfig, backend: SharedBackend) -> Self {
        Self { cfg, backend }
    }

    pub fn new(cfg: RateLimitConfig) -> Self {
        Self::with_backend(cfg, GLOBAL_BACKEND.clone())
    }

    /// 创建默认配置(从 env 读),用全局后端。
    pub fn from_env() -> Self {
        Self::new(RateLimitConfig::from_env())
    }

    /// 对应 Python `_check_rate_limit`。命中锁定 → `Err(RateLimited)`。
    ///
    /// **只读**探测锁定标记 —— 不消耗失败额度(只有真失败才计)。
    pub async fn check(&self, ip: &str, username: &str) -> Result<(), RateLimited> {
        let bucket = bucket_key(ip, username);
        if self.is_locked(&bucket).await {
            return Err(RateLimited {
                retry_after_sec: self.cfg.lockout.as_secs(),
                key: bucket,
            });
        }
        Ok(())
    }

    /// 锁定探测:锁定标记用并发计数(0/1)表达,`count > 0` 即锁定中(见 [`record_fail`])。
    async fn is_locked(&self, bucket: &str) -> bool {
        // 锁定标记用并发计数表达:record_fail 触发锁定时 incr 到 1(带 TTL=lockout 兜底,
        // Redis 后端自动过期;Memory 后端由 record_success/窗口逻辑清理)。count>0 = 锁定。
        self.backend.concurrent_count(&lock_key(bucket)).await > 0
    }

    /// 对应 Python `_record_login_fail`。返回当前窗口内失败次数;到阈值(`count >= max_fails`)置锁定标记。
    ///
    /// 失败计数用 backend 的并发计数器承载(`incr` + 读 `count`),与 Python 的
    /// `bucket.push; if len >= max_fails: lock` 逐字对齐。计数器带 TTL 兜底(Redis 后端
    /// 自动过期 ≈ window),Memory 后端靠 record_success 清理。
    pub async fn record_fail(&self, ip: &str, username: &str) -> u32 {
        let bucket = bucket_key(ip, username);
        let fkey = fail_key(&bucket);
        // 登记一次失败(用大上限只为计数,不靠它拒绝)。
        self.backend.incr_concurrent(&fkey, u32::MAX).await;
        let count = self.backend.concurrent_count(&fkey).await;
        if count >= self.cfg.max_fails {
            // 达阈值 → 置锁定标记(并发计数 0→1,max=1 即可)。
            self.backend.incr_concurrent(&lock_key(&bucket), 1).await;
        }
        count
    }

    /// 对应 Python `_record_login_success` — 清空该 key 失败计数 + 锁定标记。
    pub async fn record_success(&self, ip: &str, username: &str) {
        let bucket = bucket_key(ip, username);
        self.zero_counter(&fail_key(&bucket)).await;
        self.zero_counter(&lock_key(&bucket)).await;
    }

    /// 把一个并发计数器归零(trait 无显式 reset,用 decr 循环;计数很小,代价可忽略)。
    async fn zero_counter(&self, key: &str) {
        let mut n = self.backend.concurrent_count(key).await;
        while n > 0 {
            self.backend.decr_concurrent(key).await;
            n -= 1;
        }
    }

    /// 对应 Python `admin_unlock(ip, username)` — admin 主动解锁。
    pub async fn admin_unlock(&self, ip: &str, username: &str) {
        self.record_success(ip, username).await;
    }
}

/// 进程级 default RateLimiter(对应 Python 顶层 `_FAIL_BUCKETS` 全局)。
pub static GLOBAL_LIMITER: once_cell::sync::Lazy<RateLimiter> =
    once_cell::sync::Lazy::new(RateLimiter::from_env);

/// 对应 Python 顶层 `admin_unlock(ip, username)` — 使用全局 limiter。
pub async fn admin_unlock(ip: &str, username: &str) {
    GLOBAL_LIMITER.admin_unlock(ip, username).await;
    // TODO: 写 login_audit 行(admin_unlock 事件) — 等 rpg-db 暴露 audit helper
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::infra::rate_limit::MemoryRateLimiter;
    use std::sync::Arc;

    fn limiter(max_fails: u32) -> RateLimiter {
        let cfg = RateLimitConfig {
            max_fails,
            window: Duration::from_secs(300),
            lockout: Duration::from_secs(60),
        };
        let be: SharedBackend = Arc::new(MemoryRateLimiter::new());
        RateLimiter::with_backend(cfg, be)
    }

    #[tokio::test]
    async fn locks_after_max_fails_and_unlocks_on_success() {
        let rl = limiter(3);
        // 未锁定时 check 放行。
        assert!(rl.check("1.2.3.4", "alice").await.is_ok());
        // 连续失败到阈值。
        rl.record_fail("1.2.3.4", "alice").await;
        rl.record_fail("1.2.3.4", "alice").await;
        rl.record_fail("1.2.3.4", "alice").await; // 第 3 次触发锁定
        // 现在 check 应被拒(锁定)。
        let err = rl.check("1.2.3.4", "alice").await.unwrap_err();
        assert!(err.retry_after_sec > 0);
        // 成功(或 admin 解锁)后清锁定。
        rl.record_success("1.2.3.4", "alice").await;
        assert!(rl.check("1.2.3.4", "alice").await.is_ok());
    }

    #[tokio::test]
    async fn different_buckets_isolated() {
        let rl = limiter(1);
        rl.record_fail("1.1.1.1", "bob").await; // bob 锁定
        assert!(rl.check("1.1.1.1", "bob").await.is_err());
        // 不同用户/IP 不受影响。
        assert!(rl.check("1.1.1.1", "carol").await.is_ok());
        assert!(rl.check("2.2.2.2", "bob").await.is_ok());
    }

    #[tokio::test]
    async fn admin_unlock_clears_lock() {
        let rl = limiter(1);
        rl.record_fail("9.9.9.9", "dave").await;
        assert!(rl.check("9.9.9.9", "dave").await.is_err());
        rl.admin_unlock("9.9.9.9", "dave").await;
        assert!(rl.check("9.9.9.9", "dave").await.is_ok());
    }
}
